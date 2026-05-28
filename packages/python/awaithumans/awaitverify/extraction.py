"""Flow B — Model Then Human extraction step.

Two flavors of provider:

  LLM providers: a single API call extracts and structures the
  document. The customer's prompt + response_schema go to the model;
  the response is validated against response_schema and used as the
  human-review input.

  OCR providers: an OCR / layout step produces raw output that does
  not match the response_schema. A required `structuring` LLM step
  then turns the raw output into structured output. The structuring
  LLM emits Pydantic-matching JSON.

The model call(s) happen on the caller's machine, not on AwaitVerify
infrastructure. Customer API keys and the full document never leave
the customer's environment (Reducto and Azure DI are the exception —
they receive the document because the customer chose to call them).
"""

from __future__ import annotations

import base64
import io
import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from awaithumans.awaitverify.errors import (
    VerifyDepsMissingError,
    VerifyError,
)
from awaithumans.awaitverify.fragmentation import load_pages
from awaithumans.providers import (
    AnthropicExtraction,
    AnthropicStructuring,
    AzureDIExtraction,
    AzureOpenAIExtraction,
    AzureOpenAIStructuring,
    DoclingExtraction,
    ExtractionConfig,
    OpenAIExtraction,
    OpenAIStructuring,
    PaddleOCRExtraction,
    ReductoExtraction,
    StructuringConfig,
)

if TYPE_CHECKING:
    from awaithumans.instance import AwaitHumans

logger = logging.getLogger("awaithumans.awaitverify.extraction")


class ExtractionFailedError(VerifyError):
    """Model returned output that did not match the response schema."""

    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(
            code="EXTRACTION_FAILED",
            message=f"Model extraction via '{provider}' did not match the response schema.",
            hint=(
                f"Provider returned: {detail}\n\n"
                "  - Check your prompt produces output matching response_schema.\n"
                "  - Ensure the model supports structured / JSON-mode output.\n"
                "  - For OpenAI: GPT-4o-2024-08-06 or newer supports strict "
                "JSON schema.\n"
                "  - For Claude: sonnet 4 or newer with tool use."
            ),
            docs_url="https://docs.awaithumans.dev/awaitverify/flows/model-then-human",
        )


class ProviderNotSupportedError(VerifyError):
    """Provider extraction config is not yet implemented."""

    def __init__(self, provider_name: str) -> None:
        super().__init__(
            code="PROVIDER_NOT_SUPPORTED_YET",
            message=f"Provider '{provider_name}' is not yet implemented in v1.",
            hint=(
                "v1 implements:\n"
                "  - OpenAIExtraction (LLM, vision + structured output)\n"
                "  - AnthropicExtraction (Claude, vision + tool use)\n"
                "  - AzureOpenAIExtraction (Azure deployment of OpenAI, "
                "vision + structured output)\n"
                "  - ReductoExtraction (/extract with response schema)\n\n"
                "Remaining providers (Azure DI, Docling, PaddleOCR) have "
                "typed config classes ready but their actual API calls land "
                "in follow-ups based on customer demand. Reach out via "
                "Discord / GitHub if you need one promoted."
            ),
            docs_url="https://docs.awaithumans.dev/awaitverify/flows/model-then-human",
        )


async def run_extraction(
    *,
    document_bytes: bytes,
    extraction: ExtractionConfig,
    response_schema: type[BaseModel],
    client: AwaitHumans,
) -> dict[str, Any]:
    """Dispatch the extraction call based on the concrete config type."""
    # ── LLM providers (single call) ────────────────────────────────
    if isinstance(extraction, OpenAIExtraction):
        return await _extract_openai(
            document_bytes=document_bytes,
            extraction=extraction,
            response_schema=response_schema,
            client=client,
        )

    if isinstance(extraction, AnthropicExtraction):
        return await _extract_anthropic(
            document_bytes=document_bytes,
            extraction=extraction,
            response_schema=response_schema,
            client=client,
        )

    if isinstance(extraction, AzureOpenAIExtraction):
        return await _extract_azure_openai(
            document_bytes=document_bytes,
            extraction=extraction,
            response_schema=response_schema,
            client=client,
        )

    if isinstance(extraction, ReductoExtraction):
        return await _extract_reducto(
            document_bytes=document_bytes,
            extraction=extraction,
            response_schema=response_schema,
            client=client,
        )

    # ── OCR providers (two-step: extraction → structuring) ────────
    if isinstance(extraction, DoclingExtraction):
        raise ProviderNotSupportedError("DoclingExtraction")
    if isinstance(extraction, PaddleOCRExtraction):
        raise ProviderNotSupportedError("PaddleOCRExtraction")
    if isinstance(extraction, AzureDIExtraction):
        raise ProviderNotSupportedError("AzureDIExtraction")

    raise ProviderNotSupportedError(type(extraction).__name__)


# ── LLM extraction: OpenAI ──────────────────────────────────────────


async def _extract_openai(
    *,
    document_bytes: bytes,
    extraction: OpenAIExtraction,
    response_schema: type[BaseModel],
    client: AwaitHumans,
) -> dict[str, Any]:
    """OpenAI vision + strict JSON-schema structured output."""
    try:
        from openai import AsyncOpenAI  # noqa: PLC0415
    except ImportError as exc:
        raise VerifyDepsMissingError("openai") from exc

    api_key, base_url, organization = _resolve_openai_credentials(extraction, client)

    sdk = AsyncOpenAI(api_key=api_key, base_url=base_url, organization=organization)
    image_contents = _document_to_openai_images(document_bytes)

    content: list[dict[str, Any]] = [{"type": "text", "text": extraction.prompt}]
    content.extend(image_contents)

    logger.info(
        "Flow B (OpenAI): model=%s, pages=%d",
        extraction.model,
        len(image_contents),
    )

    response = await sdk.chat.completions.create(
        model=extraction.model,
        messages=[{"role": "user", "content": content}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "verify_document_extraction",
                "schema": response_schema.model_json_schema(),
                "strict": True,
            },
        },
    )

    raw = response.choices[0].message.content or ""
    try:
        parsed: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionFailedError("openai", f"non-JSON content: {raw[:200]}") from exc

    try:
        response_schema.model_validate(parsed)
    except Exception as exc:
        raise ExtractionFailedError("openai", str(exc)) from exc

    return parsed


def _resolve_openai_credentials(
    extraction: OpenAIExtraction,
    client: AwaitHumans,
) -> tuple[str, str | None, str | None]:
    creds = client.openai
    api_key = extraction.api_key or (creds.api_key if creds else None)
    base_url = extraction.base_url or (creds.base_url if creds else None)
    organization = creds.organization if creds else None

    if not api_key:
        raise VerifyError(
            code="EXTRACTION_API_KEY_MISSING",
            message="Flow B with OpenAIExtraction requires an OpenAI API key.",
            hint=(
                "Set the key in one of two places:\n\n"
                "  1. On the AwaitHumans client (recommended — set once):\n\n"
                "     from awaithumans.providers import OpenAI\n"
                "     client = AwaitHumans(\n"
                "         api_key='ah_sk_...',\n"
                "         openai=OpenAI(api_key='sk-...'),\n"
                "     )\n\n"
                "  2. Per call on OpenAIExtraction (overrides 1):\n\n"
                "     OpenAIExtraction(\n"
                "         model='gpt-5', prompt='...',\n"
                "         api_key='sk-...',\n"
                "     )"
            ),
            docs_url="https://docs.awaithumans.dev/awaitverify/flows/model-then-human",
        )

    return api_key, base_url, organization


def _document_to_openai_images(document_bytes: bytes) -> list[dict[str, Any]]:
    """Convert document bytes into OpenAI vision `image_url` content blocks."""
    pages = load_pages(document_bytes)
    blocks: list[dict[str, Any]] = []
    for page in pages:
        buf = io.BytesIO()
        page.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            }
        )
    return blocks


# ── LLM extraction: Azure OpenAI ────────────────────────────────────


async def _extract_azure_openai(
    *,
    document_bytes: bytes,
    extraction: AzureOpenAIExtraction,
    response_schema: type[BaseModel],
    client: AwaitHumans,
) -> dict[str, Any]:
    """Azure OpenAI vision + strict JSON-schema structured output.

    Same API contract as OpenAI — Azure deployments host OpenAI's
    models and expose the same chat.completions endpoint. The only
    differences are the client class (AsyncAzureOpenAI), the
    api_version parameter, and that `model` here is the Azure
    deployment name (not the underlying OpenAI model id).
    """
    try:
        from openai import AsyncAzureOpenAI  # noqa: PLC0415
    except ImportError as exc:
        raise VerifyDepsMissingError("openai") from exc

    api_key, endpoint, api_version = _resolve_azure_openai_credentials(extraction, client)

    sdk = AsyncAzureOpenAI(
        api_key=api_key,
        api_version=api_version,
        azure_endpoint=endpoint,
    )
    image_contents = _document_to_openai_images(document_bytes)

    content: list[dict[str, Any]] = [{"type": "text", "text": extraction.prompt}]
    content.extend(image_contents)

    logger.info(
        "Flow B (Azure OpenAI): deployment=%s, pages=%d",
        extraction.model,
        len(image_contents),
    )

    response = await sdk.chat.completions.create(
        model=extraction.model,  # Azure deployment name
        messages=[{"role": "user", "content": content}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "verify_document_extraction",
                "schema": response_schema.model_json_schema(),
                "strict": True,
            },
        },
    )

    raw = response.choices[0].message.content or ""
    try:
        parsed: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionFailedError("azure_openai", f"non-JSON content: {raw[:200]}") from exc

    try:
        response_schema.model_validate(parsed)
    except Exception as exc:
        raise ExtractionFailedError("azure_openai", str(exc)) from exc

    return parsed


def _resolve_azure_openai_credentials(
    extraction: AzureOpenAIExtraction,
    client: AwaitHumans,
) -> tuple[str, str, str]:
    creds = client.azure_openai
    api_key = extraction.api_key or (creds.api_key if creds else None)
    endpoint = extraction.endpoint or (creds.endpoint if creds else None)
    api_version = extraction.api_version or (creds.api_version if creds else None)

    if not api_key or not endpoint or not api_version:
        raise VerifyError(
            code="EXTRACTION_AZURE_OPENAI_CONFIG_MISSING",
            message=(
                "Flow B with AzureOpenAIExtraction requires api_key, endpoint, and api_version."
            ),
            hint=(
                "Set them in one of two places:\n\n"
                "  1. On the AwaitHumans client (recommended):\n\n"
                "     from awaithumans.providers import AzureOpenAI\n"
                "     client = AwaitHumans(\n"
                "         api_key='ah_sk_...',\n"
                "         azure_openai=AzureOpenAI(\n"
                "             api_key='...',\n"
                "             endpoint='https://YOUR_RESOURCE.openai.azure.com',\n"
                "             api_version='2024-10-21',\n"
                "         ),\n"
                "     )\n\n"
                "  2. Per call on AzureOpenAIExtraction (overrides 1 per field):\n\n"
                "     AzureOpenAIExtraction(\n"
                "         model='your-deployment-name', prompt='...',\n"
                "         api_key='...', endpoint='...', api_version='...',\n"
                "     )\n\n"
                "On Azure, `model` is the deployment name configured in your "
                "Azure portal, NOT the underlying OpenAI model id."
            ),
            docs_url="https://docs.awaithumans.dev/awaitverify/flows/model-then-human",
        )

    return api_key, endpoint, api_version


# ── LLM extraction: Reducto ─────────────────────────────────────────


async def _extract_reducto(
    *,
    document_bytes: bytes,
    extraction: ReductoExtraction,
    response_schema: type[BaseModel],
    client: AwaitHumans,
) -> dict[str, Any]:
    """Reducto `/extract` — schema-driven structured extraction.

    Reducto's extract endpoint accepts a response schema and returns
    JSON matching it directly. Like the LLM providers, this is a
    single-call flow with no separate structuring step.

    See https://docs.reducto.ai/extract/overview for the endpoint
    contract. The exact SDK method names below follow the standard
    Reducto Python SDK pattern — if Reducto ships a different shape
    we adjust here only.
    """
    try:
        from reducto import AsyncReducto  # noqa: PLC0415
    except ImportError as exc:
        raise VerifyDepsMissingError("reducto") from exc

    api_key, base_url = _resolve_reducto_credentials(extraction, client)

    sdk_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        sdk_kwargs["base_url"] = base_url
    sdk = AsyncReducto(**sdk_kwargs)

    logger.info("Flow B (Reducto): model=%s", extraction.model)

    # Reducto accepts an uploaded file plus an extraction schema +
    # instructions. The document is sent to Reducto (the customer's
    # explicit choice when selecting this provider) and the
    # structured JSON comes back.
    response = await sdk.extract.run(
        document=document_bytes,
        schema=response_schema.model_json_schema(),
        instructions=extraction.prompt,
        model=extraction.model,
    )

    # Reducto returns the structured result on `.data` (per their
    # standard response envelope). Defensive: accept either the
    # envelope shape or a raw dict.
    parsed: Any
    if hasattr(response, "data"):
        parsed = response.data
    elif isinstance(response, dict):
        parsed = response.get("data", response)
    else:
        raise ExtractionFailedError(
            "reducto", f"unrecognized response shape: {type(response).__name__}"
        )

    if not isinstance(parsed, dict):
        raise ExtractionFailedError("reducto", f"expected dict, got {type(parsed).__name__}")

    try:
        response_schema.model_validate(parsed)
    except Exception as exc:
        raise ExtractionFailedError("reducto", str(exc)) from exc

    return parsed


def _resolve_reducto_credentials(
    extraction: ReductoExtraction,
    client: AwaitHumans,
) -> tuple[str, str | None]:
    creds = client.reducto
    api_key = extraction.api_key or (creds.api_key if creds else None)
    base_url = extraction.base_url or (creds.base_url if creds else None)

    if not api_key:
        raise VerifyError(
            code="EXTRACTION_API_KEY_MISSING",
            message="Flow B with ReductoExtraction requires a Reducto API key.",
            hint=(
                "Set the key in one of two places:\n\n"
                "  1. On the AwaitHumans client (recommended):\n\n"
                "     from awaithumans.providers import Reducto\n"
                "     client = AwaitHumans(\n"
                "         api_key='ah_sk_...',\n"
                "         reducto=Reducto(api_key='red-...'),\n"
                "     )\n\n"
                "  2. Per call on ReductoExtraction:\n\n"
                "     ReductoExtraction(\n"
                "         prompt='...',\n"
                "         api_key='red-...',\n"
                "     )"
            ),
            docs_url="https://docs.awaithumans.dev/awaitverify/flows/model-then-human",
        )

    return api_key, base_url


# ── LLM extraction: Anthropic (Claude) ──────────────────────────────


async def _extract_anthropic(
    *,
    document_bytes: bytes,
    extraction: AnthropicExtraction,
    response_schema: type[BaseModel],
    client: AwaitHumans,
) -> dict[str, Any]:
    """Claude vision + tool-use for structured output."""
    try:
        from anthropic import AsyncAnthropic  # noqa: PLC0415
    except ImportError as exc:
        raise VerifyDepsMissingError("anthropic") from exc

    api_key, base_url = _resolve_anthropic_credentials(extraction, client)

    sdk = AsyncAnthropic(api_key=api_key, base_url=base_url)
    image_blocks = _document_to_anthropic_images(document_bytes)

    tool_name = "submit_extraction"
    tool = {
        "name": tool_name,
        "description": (
            "Submit the structured extraction from the document. Output "
            "must match the provided JSON schema exactly."
        ),
        "input_schema": response_schema.model_json_schema(),
    }

    message_content: list[dict[str, Any]] = [{"type": "text", "text": extraction.prompt}]
    message_content.extend(image_blocks)

    logger.info(
        "Flow B (Anthropic): model=%s, pages=%d",
        extraction.model,
        len(image_blocks),
    )

    response = await sdk.messages.create(
        model=extraction.model,
        max_tokens=4096,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": message_content}],
    )

    tool_use_block = next(
        (block for block in response.content if getattr(block, "type", None) == "tool_use"),
        None,
    )
    if tool_use_block is None:
        raise ExtractionFailedError("anthropic", "no tool_use block in response")

    parsed = tool_use_block.input if isinstance(tool_use_block.input, dict) else {}
    try:
        response_schema.model_validate(parsed)
    except Exception as exc:
        raise ExtractionFailedError("anthropic", str(exc)) from exc

    return parsed


def _resolve_anthropic_credentials(
    extraction: AnthropicExtraction,
    client: AwaitHumans,
) -> tuple[str, str | None]:
    creds = client.anthropic
    api_key = extraction.api_key or (creds.api_key if creds else None)
    base_url = extraction.base_url or (creds.base_url if creds else None)

    if not api_key:
        raise VerifyError(
            code="EXTRACTION_API_KEY_MISSING",
            message="Flow B with AnthropicExtraction requires an Anthropic API key.",
            hint=(
                "Set the key in one of two places:\n\n"
                "  1. On the AwaitHumans client (recommended):\n\n"
                "     from awaithumans.providers import Anthropic\n"
                "     client = AwaitHumans(\n"
                "         api_key='ah_sk_...',\n"
                "         anthropic=Anthropic(api_key='sk-ant-...'),\n"
                "     )\n\n"
                "  2. Per call on AnthropicExtraction:\n\n"
                "     AnthropicExtraction(\n"
                "         model='claude-sonnet-4-5', prompt='...',\n"
                "         api_key='sk-ant-...',\n"
                "     )"
            ),
            docs_url="https://docs.awaithumans.dev/awaitverify/flows/model-then-human",
        )

    return api_key, base_url


def _document_to_anthropic_images(document_bytes: bytes) -> list[dict[str, Any]]:
    """Convert document bytes into Anthropic vision content blocks."""
    pages = load_pages(document_bytes)
    blocks: list[dict[str, Any]] = []
    for page in pages:
        buf = io.BytesIO()
        page.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64,
                },
            }
        )
    return blocks


# ── Structuring step dispatcher (used by OCR providers) ─────────────


async def run_structuring(
    *,
    raw_extraction: str,
    structuring: StructuringConfig,
    response_schema: type[BaseModel],
    client: AwaitHumans,
) -> dict[str, Any]:
    """Turn raw OCR output into a response_schema-matching dict.

    Used by OCR providers (Docling, PaddleOCR, Reducto, Azure DI) once
    they have produced raw extraction. Dispatches on the concrete
    StructuringConfig subclass.
    """
    if isinstance(structuring, OpenAIStructuring):
        ext = OpenAIExtraction(
            model=structuring.model,
            prompt=f"{structuring.prompt}\n\nRaw extraction to structure:\n{raw_extraction}",
            api_key=structuring.api_key,
            base_url=structuring.base_url,
        )
        return await _extract_openai_text_only(
            extraction=ext,
            response_schema=response_schema,
            client=client,
        )

    if isinstance(structuring, AnthropicStructuring):
        raise ProviderNotSupportedError("AnthropicStructuring")
    if isinstance(structuring, AzureOpenAIStructuring):
        raise ProviderNotSupportedError("AzureOpenAIStructuring")

    raise ProviderNotSupportedError(type(structuring).__name__)


async def _extract_openai_text_only(
    *,
    extraction: OpenAIExtraction,
    response_schema: type[BaseModel],
    client: AwaitHumans,
) -> dict[str, Any]:
    """Text-only OpenAI structured-output call (no images).

    Used for the structuring step after an OCR provider has already
    converted the document to text / layout JSON.
    """
    try:
        from openai import AsyncOpenAI  # noqa: PLC0415
    except ImportError as exc:
        raise VerifyDepsMissingError("openai") from exc

    api_key, base_url, organization = _resolve_openai_credentials(extraction, client)
    sdk = AsyncOpenAI(api_key=api_key, base_url=base_url, organization=organization)

    response = await sdk.chat.completions.create(
        model=extraction.model,
        messages=[{"role": "user", "content": extraction.prompt}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "verify_document_structuring",
                "schema": response_schema.model_json_schema(),
                "strict": True,
            },
        },
    )

    raw = response.choices[0].message.content or ""
    try:
        parsed: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionFailedError("openai", f"non-JSON content: {raw[:200]}") from exc

    try:
        response_schema.model_validate(parsed)
    except Exception as exc:
        raise ExtractionFailedError("openai", str(exc)) from exc

    return parsed


__all__ = [
    "run_extraction",
    "run_structuring",
    "ExtractionFailedError",
    "ProviderNotSupportedError",
]
