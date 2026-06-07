"""LLM-driven schema proposer for the AwaitVerify landing demo.

Takes a single page PNG and asks Azure OpenAI to propose a small
Pydantic schema (3..7 fields) appropriate for the document on the page.
Uses the same vision pattern as `extractor.py` but with a separate,
faster deployment name (`AZURE_OPENAI_SCHEMA_DEPLOYMENT`), falling
back to the main deployment when unset.

The schema proposal is intentionally a separate call from extraction:
the visitor sees a "Reading your document..." step, then an editable
schema, then the actual extraction runs against the (possibly edited)
schema. The proposer never persists anything; it is a lightweight
pre-flight aid.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import TYPE_CHECKING, Any

from awaithumans.server.core.config import settings
from awaithumans.server.services.demo.exceptions import (
    DemoProviderError,
    DemoSchemaError,
)
from awaithumans.server.services.demo.schema_builder import (
    SchemaSpec,
    spec_from_json,
)

if TYPE_CHECKING:
    from openai import AsyncAzureOpenAI

logger = logging.getLogger("awaithumans.server.services.demo.schema_proposer")

_MAX_TOKENS = 800
_PROVIDER_LABEL = "Schema proposer"

_SUPPORTED_TYPES = ("str", "int", "float", "bool", "date", "list[str]")

_SYSTEM_PROMPT = (
    "You are an expert document analyst. Given an image of a single "
    "page from a document, propose a small Pydantic schema describing "
    "the structured fields a human would want to extract from that page. "
    "Rules: "
    "(1) Propose between 3 and 7 fields, no more, no less. "
    "(2) Each field name must be valid snake_case (lowercase, digits, "
    "and underscores only; starting with a letter). "
    "(3) Each field type must be one of: str, int, float, bool, date, "
    "list[str]. No other types. Use 'int' for monetary cents-style "
    "integers, 'date' for ISO dates, 'list[str]' for short tag lists. "
    "(4) The schema name must be a CamelCase Python identifier "
    "describing the document (e.g. Invoice, Receipt, GovernmentID, "
    "PurchaseOrder). "
    "(5) Return ONLY a JSON object. No prose, no markdown, no code "
    "fences. The object must match this exact shape: "
    '{"name": "<CamelCase>", "fields": [{"name": "<snake_case>", '
    '"type": "<one of the supported types>"}, ...]}. '
    "Pick the most useful fields for the document, not every possible "
    "field. Quality over quantity."
)


_USER_PROMPT = (
    "Look at the page image and propose a Pydantic schema describing "
    "the most useful structured fields on that page. "
    "Return ONLY a JSON object. No prose, no markdown, no code fences."
)


def _build_client() -> AsyncAzureOpenAI:
    """Construct an Azure OpenAI client.

    Mirrors `extractor._build_client`: all three of endpoint +
    deployment-resolution + api_key must be set; missing any one of
    them surfaces as `DemoProviderError` so the wizard shows a
    generic "schema proposer unavailable" message rather than a
    stack trace.
    """
    try:
        from openai import AsyncAzureOpenAI  # noqa: PLC0415
    except ImportError as exc:
        logger.warning("OpenAI SDK not installed: %s", exc)
        raise DemoProviderError(_PROVIDER_LABEL) from exc

    deployment = settings.AZURE_OPENAI_SCHEMA_DEPLOYMENT or settings.AZURE_OPENAI_DEPLOYMENT

    if not (settings.AZURE_OPENAI_API_KEY and settings.AZURE_OPENAI_ENDPOINT and deployment):
        raise DemoProviderError(_PROVIDER_LABEL)

    return AsyncAzureOpenAI(
        api_key=settings.AZURE_OPENAI_API_KEY,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_version=settings.AZURE_OPENAI_API_VERSION,
    )


def _resolve_deployment() -> str:
    """Resolve the deployment to use for schema proposals.

    The schema deployment is optional. When unset we fall back to the
    main extractor deployment so single-deployment setups still work.
    """
    return settings.AZURE_OPENAI_SCHEMA_DEPLOYMENT or settings.AZURE_OPENAI_DEPLOYMENT or ""


async def propose_schema_from_page(page_png: bytes) -> SchemaSpec:
    """Ask the LLM to propose a `SchemaSpec` for the given page.

    Raises `DemoProviderError` on any provider/parse/validation
    failure so the route layer maps it to a sanitized 502. The real
    error is logged server-side.
    """
    try:
        client = _build_client()
        image_b64 = base64.standard_b64encode(page_png).decode("ascii")
        # The Azure Responses-API-style deployment rejects the legacy
        # `response_format` parameter; the system prompt is hardened to
        # require a bare JSON object instead.
        response = await client.chat.completions.create(
            model=_resolve_deployment(),
            max_tokens=_MAX_TOKENS,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_b64}",
                            },
                        },
                        {"type": "text", "text": _USER_PROMPT},
                    ],
                },
            ],
        )
    except DemoProviderError:
        raise
    except Exception as exc:
        logger.warning("Schema proposer call failed: %s", exc)
        raise DemoProviderError(_PROVIDER_LABEL) from exc

    raw_text = _extract_text(response)
    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Schema proposer returned non-JSON: %r", raw_text[:200])
        raise DemoProviderError(_PROVIDER_LABEL) from exc

    if not isinstance(parsed, dict):
        logger.warning("Schema proposer JSON wasn't an object: %r", parsed)
        raise DemoProviderError(_PROVIDER_LABEL)

    # Defensive normalization: clip to the supported field count + type
    # allowlist before delegating to the validating helper. If the model
    # ignored the prompt and returned types like "string", we drop those
    # rather than pass them through to the schema validator.
    _normalize_in_place(parsed)

    try:
        spec = spec_from_json(parsed)
    except DemoSchemaError as exc:
        logger.warning("Schema proposer returned invalid spec: %s", exc)
        raise DemoProviderError(_PROVIDER_LABEL) from exc

    return spec


def _normalize_in_place(parsed: dict[str, Any]) -> None:
    """Best-effort normalisation of the raw LLM JSON before validation.

    Drops fields with unsupported types so a single bad guess from the
    model doesn't tank the whole proposal. The schema_builder's strict
    validator handles the rest (identifier-shape, dupes, count caps).
    """
    fields = parsed.get("fields")
    if not isinstance(fields, list):
        return
    cleaned: list[dict[str, Any]] = []
    for raw in fields:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        type_ = raw.get("type")
        if not isinstance(name, str) or not isinstance(type_, str):
            continue
        if type_ not in _SUPPORTED_TYPES:
            continue
        cleaned.append({"name": name, "type": type_})
    parsed["fields"] = cleaned


def _extract_text(response: Any) -> str:
    """Pull the assistant text out of a ChatCompletion response.

    Tolerates both the real OpenAI SDK shape and minimal test doubles;
    matches `extractor._extract_text` so we share behavior across the
    two LLM call sites.
    """
    choices = getattr(response, "choices", None)
    if not choices:
        return ""
    first = choices[0]
    message = getattr(first, "message", None)
    if message is None:
        return ""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    return ""
