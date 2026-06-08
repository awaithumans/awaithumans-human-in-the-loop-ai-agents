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
                "  - OpenAIExtraction (LLM, vision + Responses API)\n"
                "  - AnthropicExtraction (Claude, vision + tool use)\n"
                "  - AzureOpenAIExtraction (Azure deployment of OpenAI, "
                "vision + Responses API)\n"
                "  - ReductoExtraction (/extract with response schema)\n"
                "  - AzureDIExtraction (Azure DI layout + structuring step)\n"
                "  - OpenAIStructuring / AzureOpenAIStructuring (used as "
                "the structuring step in OCR pipelines)\n\n"
                "Remaining providers (Docling, PaddleOCR, "
                "AnthropicStructuring) have typed config classes ready but "
                "their actual API calls land in follow-ups based on customer "
                "demand. Reach out via Discord / GitHub if you need one "
                "promoted."
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
    if isinstance(extraction, AzureDIExtraction):
        return await _extract_azure_di(
            document_bytes=document_bytes,
            extraction=extraction,
            response_schema=response_schema,
            client=client,
        )

    if isinstance(extraction, DoclingExtraction):
        raise ProviderNotSupportedError("DoclingExtraction")
    if isinstance(extraction, PaddleOCRExtraction):
        raise ProviderNotSupportedError("PaddleOCRExtraction")

    raise ProviderNotSupportedError(type(extraction).__name__)


# ── LLM extraction: OpenAI ──────────────────────────────────────────


async def _extract_openai(
    *,
    document_bytes: bytes,
    extraction: OpenAIExtraction,
    response_schema: type[BaseModel],
    client: AwaitHumans,
) -> dict[str, Any]:
    """OpenAI vision + JSON-schema structured output via the Responses API.

    Uses sdk.responses.create rather than chat.completions so the same
    code path serves gpt-4o, gpt-5, and o-series models. chat.completions
    rejects response_format on gpt-5: 'Unsupported parameter:
    response_format. In the Responses API, this parameter has moved to
    text.format.'"""
    try:
        from openai import AsyncOpenAI  # noqa: PLC0415
    except ImportError as exc:
        raise VerifyDepsMissingError("openai") from exc

    api_key, base_url, organization = _resolve_openai_credentials(extraction, client)
    sdk = AsyncOpenAI(api_key=api_key, base_url=base_url, organization=organization)
    image_inputs = _document_to_openai_responses_image_inputs(document_bytes)

    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": extraction.prompt},
        *image_inputs,
    ]

    logger.info(
        "Flow B (OpenAI): model=%s, pages=%d",
        extraction.model,
        len(image_inputs),
    )

    return await _run_openai_responses_extract(
        sdk=sdk,
        model=extraction.model,
        content=content,
        response_schema=response_schema,
        provider_label="openai",
    )


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


def _document_to_openai_responses_image_inputs(
    document_bytes: bytes,
) -> list[dict[str, Any]]:
    """Convert document bytes into OpenAI Responses-API `input_image` blocks.

    `detail` is set explicitly: the SDK's TypedDict marks it Required
    and the server-side behavior on omitted `detail` is undefined
    (could pick high — expensive — or low — degraded). Azure tolerates
    omission today, but OpenAI direct is stricter on the same contract."""
    pages = load_pages(document_bytes)
    blocks: list[dict[str, Any]] = []
    for page in pages:
        buf = io.BytesIO()
        page.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        blocks.append(
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{b64}",
                "detail": "auto",
            }
        )
    return blocks


async def _run_openai_responses_extract(
    *,
    sdk: Any,
    model: str,
    content: list[dict[str, Any]],
    response_schema: type[BaseModel],
    provider_label: str,
) -> dict[str, Any]:
    """Shared Responses-API call → parsed dict validated against response_schema.

    Strict mode is off because customer Pydantic schemas commonly include
    Optional fields and additional properties, which OpenAI's strict mode
    rejects. The schema is still enforced by `model_validate` below.

    Accepts either AsyncOpenAI or AsyncAzureOpenAI; both expose the same
    `.responses.create()` shape."""
    response = await sdk.responses.create(
        model=model,
        input=[{"role": "user", "content": content}],
        text={
            "format": {
                "type": "json_schema",
                "name": "verify_document_extraction",
                "schema": response_schema.model_json_schema(),
                "strict": False,
            }
        },
    )

    raw = response.output_text or ""
    try:
        parsed: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionFailedError(provider_label, f"non-JSON content: {raw[:200]}") from exc

    try:
        response_schema.model_validate(parsed)
    except Exception as exc:
        raise ExtractionFailedError(provider_label, str(exc)) from exc

    return parsed


# ── LLM extraction: Azure OpenAI ────────────────────────────────────


async def _extract_azure_openai(
    *,
    document_bytes: bytes,
    extraction: AzureOpenAIExtraction,
    response_schema: type[BaseModel],
    client: AwaitHumans,
) -> dict[str, Any]:
    """Azure OpenAI vision + JSON-schema via the Responses API.

    Azure deployments expose the OpenAI Responses API at the same shape
    as openai.com. Required for GPT-5.x deployments, which reject the
    older chat.completions + response_format path with: 'Unsupported
    parameter: response_format. In the Responses API, this parameter
    has moved to text.format.'

    `model` here is the Azure deployment name configured in the Azure
    portal, not an OpenAI model id."""
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
    image_inputs = _document_to_openai_responses_image_inputs(document_bytes)

    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": extraction.prompt},
        *image_inputs,
    ]

    logger.info(
        "Flow B (Azure OpenAI): deployment=%s, pages=%d",
        extraction.model,
        len(image_inputs),
    )

    return await _run_openai_responses_extract(
        sdk=sdk,
        model=extraction.model,  # Azure deployment name
        content=content,
        response_schema=response_schema,
        provider_label="azure_openai",
    )


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
    """Reducto `/upload` then `/extract` — schema-driven structured extraction.

    Reducto's v3 pipeline is two calls: upload the document bytes,
    then invoke /extract with the returned `reducto://` URL. The
    schema is passed inside `instructions`; the customer's prompt
    becomes the `system_prompt`. Reducto returns the schema-validated
    JSON in `result`.

    Reducto receives the document because the customer chose this
    provider (same trust contract as Azure DI)."""
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

    # Step 1: upload the document. Reducto accepts a (name, bytes)
    # tuple; the file extension hint helps their parser pick a backend
    # (PDF parsing vs image OCR vs Office formats). We sniff the
    # leading magic bytes so callers don't have to pass an extension —
    # Reducto rejects mislabeled documents with 415 DOCUMENT_CORRUPT.
    upload = await sdk.upload(file=(_reducto_filename_for(document_bytes), document_bytes))
    file_url = upload.presigned_url or f"reducto://{upload.file_id}"

    # Step 2: extract with the schema. Reducto's v3 contract puts the
    # JSON schema and the natural-language prompt inside `instructions`.
    response = await sdk.extract.run(
        input=file_url,
        instructions={
            "schema": response_schema.model_json_schema(),
            "system_prompt": extraction.prompt,
        },
    )

    # Sync (V3Extract) response carries .result. Async response would
    # carry a job_id — we don't poll here; customers who want async
    # should call Reducto themselves.
    result = getattr(response, "result", None)
    if result is None:
        raise ExtractionFailedError(
            "reducto",
            f"missing 'result' in {type(response).__name__}",
        )

    # Reducto returns a list when `array_extract` is on, a dict
    # otherwise. We don't enable array_extract here, so a list is
    # unexpected — pick the first element defensively.
    parsed: Any = result[0] if isinstance(result, list) and result else result
    if not isinstance(parsed, dict):
        raise ExtractionFailedError("reducto", f"expected dict result, got {type(parsed).__name__}")

    try:
        response_schema.model_validate(parsed)
    except Exception as exc:
        raise ExtractionFailedError("reducto", str(exc)) from exc

    return parsed


def _reducto_filename_for(document_bytes: bytes) -> str:
    """Pick a filename whose extension matches the document's magic bytes.

    Reducto's `/upload` rejects PDFs sent as ".png" and vice versa
    (HTTP 415 DOCUMENT_CORRUPT), so we sniff the leading bytes rather
    than trust the caller. Unknown formats fall back to ".pdf" — the
    most common AwaitVerify input."""
    head = document_bytes[:8]
    if head.startswith(b"%PDF"):
        return "document.pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "document.png"
    if head[:3] == b"\xff\xd8\xff":
        return "document.jpg"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "document.tiff"
    return "document.pdf"


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


# ── OCR extraction: Azure Document Intelligence ─────────────────────


async def _extract_azure_di(
    *,
    document_bytes: bytes,
    extraction: AzureDIExtraction,
    response_schema: type[BaseModel],
    client: AwaitHumans,
) -> dict[str, Any]:
    """Azure Document Intelligence + a structuring LLM step.

    Calls Azure DI's analyze endpoint (default model: prebuilt-layout)
    to extract layout-aware Markdown, then runs that Markdown through
    the configured StructuringConfig — typically OpenAIStructuring or
    AzureOpenAIStructuring — to produce response_schema-matching JSON.

    Azure DI receives the document because the customer chose this
    provider. Customer DI credentials are used locally; only the
    structured result reaches AwaitVerify (same Flow B contract as
    Reducto)."""
    try:
        from azure.ai.documentintelligence.aio import (  # noqa: PLC0415
            DocumentIntelligenceClient,
        )
        from azure.ai.documentintelligence.models import (  # noqa: PLC0415
            AnalyzeDocumentRequest,
        )
        from azure.core.credentials import AzureKeyCredential  # noqa: PLC0415
    except ImportError as exc:
        raise VerifyDepsMissingError("azure-ai-documentintelligence") from exc

    api_key, endpoint = _resolve_azure_di_credentials(extraction, client)

    logger.info("Flow B (Azure DI): model=%s", extraction.model)

    di_client = DocumentIntelligenceClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(api_key),
    )
    try:
        poller = await di_client.begin_analyze_document(
            model_id=extraction.model,
            body=AnalyzeDocumentRequest(bytes_source=document_bytes),
            output_content_format="markdown",
        )
        result = await poller.result()
    finally:
        await di_client.close()

    raw = getattr(result, "content", None) or ""
    if not raw.strip():
        raise ExtractionFailedError("azure_di", "Azure DI returned empty content.")

    return await run_structuring(
        raw_extraction=raw,
        structuring=extraction.structuring,
        response_schema=response_schema,
        client=client,
    )


def _resolve_azure_di_credentials(
    extraction: AzureDIExtraction,
    client: AwaitHumans,
) -> tuple[str, str]:
    creds = client.azure_di
    api_key = extraction.api_key or (creds.api_key if creds else None)
    endpoint = extraction.endpoint or (creds.endpoint if creds else None)

    if not api_key or not endpoint:
        raise VerifyError(
            code="EXTRACTION_AZURE_DI_CONFIG_MISSING",
            message=("Flow B with AzureDIExtraction requires api_key and endpoint."),
            hint=(
                "Set them in one of two places:\n\n"
                "  1. On the AwaitHumans client (recommended):\n\n"
                "     from awaithumans.providers import AzureDI\n"
                "     client = AwaitHumans(\n"
                "         api_key='ah_sk_...',\n"
                "         azure_di=AzureDI(\n"
                "             api_key='...',\n"
                "             endpoint='https://YOUR_RESOURCE.cognitiveservices.azure.com',\n"
                "         ),\n"
                "     )\n\n"
                "  2. Per call on AzureDIExtraction:\n\n"
                "     AzureDIExtraction(\n"
                "         model='prebuilt-layout',\n"
                "         structuring=OpenAIStructuring(model='gpt-4o', prompt='...'),\n"
                "         api_key='...', endpoint='...',\n"
                "     )"
            ),
            docs_url="https://docs.awaithumans.dev/awaitverify/flows/model-then-human",
        )

    return api_key, endpoint


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

    if isinstance(structuring, AzureOpenAIStructuring):
        return await _structure_azure_openai_text_only(
            structuring=structuring,
            raw_extraction=raw_extraction,
            response_schema=response_schema,
            client=client,
        )

    if isinstance(structuring, AnthropicStructuring):
        raise ProviderNotSupportedError("AnthropicStructuring")

    raise ProviderNotSupportedError(type(structuring).__name__)


async def _extract_openai_text_only(
    *,
    extraction: OpenAIExtraction,
    response_schema: type[BaseModel],
    client: AwaitHumans,
) -> dict[str, Any]:
    """Text-only OpenAI structured-output call via the Responses API.

    Used for the structuring step after an OCR provider has already
    converted the document to text / layout JSON. No images."""
    try:
        from openai import AsyncOpenAI  # noqa: PLC0415
    except ImportError as exc:
        raise VerifyDepsMissingError("openai") from exc

    api_key, base_url, organization = _resolve_openai_credentials(extraction, client)
    sdk = AsyncOpenAI(api_key=api_key, base_url=base_url, organization=organization)

    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": extraction.prompt},
    ]

    return await _run_openai_responses_extract(
        sdk=sdk,
        model=extraction.model,
        content=content,
        response_schema=response_schema,
        provider_label="openai",
    )


async def _structure_azure_openai_text_only(
    *,
    structuring: AzureOpenAIStructuring,
    raw_extraction: str,
    response_schema: type[BaseModel],
    client: AwaitHumans,
) -> dict[str, Any]:
    """Text-only Azure OpenAI structuring call via the Responses API.

    Lets an OCR pipeline (Azure DI, future Docling/PaddleOCR) close the
    loop using an Azure deployment instead of OpenAI direct — important
    for customers whose policy keeps OpenAI usage inside Azure."""
    try:
        from openai import AsyncAzureOpenAI  # noqa: PLC0415
    except ImportError as exc:
        raise VerifyDepsMissingError("openai") from exc

    # Build an AzureOpenAIExtraction-shaped credential bundle so we can
    # reuse the same resolver as Flow B extraction.
    proxy_extraction = AzureOpenAIExtraction(
        model=structuring.model,
        prompt="",  # not used by the resolver
        api_key=structuring.api_key,
        endpoint=structuring.endpoint,
        api_version=structuring.api_version,
    )
    api_key, endpoint, api_version = _resolve_azure_openai_credentials(proxy_extraction, client)
    sdk = AsyncAzureOpenAI(
        api_key=api_key,
        api_version=api_version,
        azure_endpoint=endpoint,
    )

    prompt = f"{structuring.prompt}\n\nRaw extraction to structure:\n{raw_extraction}"
    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": prompt},
    ]

    return await _run_openai_responses_extract(
        sdk=sdk,
        model=structuring.model,  # Azure deployment name
        content=content,
        response_schema=response_schema,
        provider_label="azure_openai",
    )


__all__ = [
    "run_extraction",
    "run_structuring",
    "ExtractionFailedError",
    "ProviderNotSupportedError",
]
