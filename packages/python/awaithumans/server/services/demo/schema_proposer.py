"""LLM-driven schema proposer for the AwaitVerify landing demo.

Takes a single page PNG and asks Azure OpenAI to propose a Pydantic
schema describing the most valuable structured data on the page.
Uses the same vision pattern as `extractor.py` but with a separate,
faster deployment name (`AZURE_OPENAI_SCHEMA_DEPLOYMENT`), falling
back to the main deployment when unset.

The schema can include nested ``record`` and ``list[record]`` fields
so document structures like address blocks and line-item tables get
captured as first-class typed structures rather than flattened into
opaque strings.

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

# Nested ``list[record]`` schemas (a table with 10+ columns and 5+
# top-level fields surrounding it) easily eat 1500+ output tokens.
# 4000 leaves headroom for the largest single-page document we
# realistically see while still bounding cost.
_MAX_TOKENS = 4000
_PROVIDER_LABEL = "Schema proposer"

_SUPPORTED_PRIMITIVE_TYPES = ("str", "int", "float", "bool", "list[str]")
_SUPPORTED_NESTED_TYPES = ("record", "list[record]")
_SUPPORTED_TYPES = _SUPPORTED_PRIMITIVE_TYPES + _SUPPORTED_NESTED_TYPES

_SYSTEM_PROMPT = (
    "You are an expert document analyst. Given an image of a single "
    "page from a document, propose a Pydantic schema describing the "
    "most valuable structured data on the page. Extract as many "
    "fields as the data warrants, no fewer, no more.\n\n"
    "Rules:\n"
    "1. Each field name must be valid snake_case (lowercase letters, "
    "digits, underscores; must start with a letter).\n"
    "2. Each field type must be one of: str, int, float, bool, "
    "list[str], record, list[record].\n"
    "3. Use `str` for date-like fields, free-form text, identifiers, "
    "and anything not clearly numeric or boolean.\n"
    "4. The schema name must be a CamelCase Python identifier "
    "describing the document.\n"
    "5. Use `record` for a nested object with its own named fields "
    "(e.g. an address block, a totals summary).\n"
    "6. Use `list[record]` for repeating row structures (e.g. tables "
    "of line items, employee rows, transaction logs). The repeated "
    "row's columns become the nested fields.\n"
    "7. For `record` and `list[record]`, include a `fields` array "
    "describing the nested fields. Nested fields follow the same "
    "rules (snake_case, supported types). Nested records can "
    "themselves contain `record` or `list[record]`.\n"
    "8. Pick the fields that carry the most useful information on "
    "the page. If the page is dominated by a table, the table should "
    "be the dominant field.\n\n"
    "Return ONLY a JSON object. No prose, no markdown, no code "
    "fences. The object matches this shape:\n\n"
    "{\n"
    '  "name": "<CamelCase>",\n'
    '  "fields": [\n'
    '    {"name": "<snake_case>", "type": "<supported>"},\n'
    '    {"name": "<snake_case>", "type": "record", "fields": [...]},\n'
    '    {"name": "<snake_case>", "type": "list[record]", "fields": [...]}\n'
    "  ]\n"
    "}"
)


_USER_PROMPT = (
    "Look at the page image and propose a Pydantic schema describing "
    "the most useful structured data on that page. "
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
        # Azure's Responses API takes `input` (not `messages`),
        # `max_output_tokens` (not `max_tokens`), and content types
        # `input_text` / `input_image`. The system prompt enforces JSON.
        response = await client.responses.create(
            model=_resolve_deployment(),
            max_output_tokens=_MAX_TOKENS,
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{image_b64}",
                        },
                        {"type": "input_text", "text": _USER_PROMPT},
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
    model doesn't tank the whole proposal. Recurses through nested
    ``record`` and ``list[record]`` fields so the same allowlist
    applies at every level. The schema_builder's strict validator
    handles identifier shape, duplicate names, and other structural
    problems after this best-effort cleanup.
    """
    parsed["fields"] = _normalize_field_list(parsed.get("fields"))


def _normalize_field_list(fields: Any) -> list[dict[str, Any]]:
    """Filter one level of fields, recursing into nested ``fields``.

    Drops any entry whose type isn't in the supported allowlist.
    Nested ``record`` / ``list[record]`` fields recurse so the
    rejection rule applies at every depth.
    """
    if not isinstance(fields, list):
        return []
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
        entry: dict[str, Any] = {"name": name, "type": type_}
        if type_ in _SUPPORTED_NESTED_TYPES:
            entry["fields"] = _normalize_field_list(raw.get("fields"))
        cleaned.append(entry)
    return cleaned


def _extract_text(response: Any) -> str:
    """Pull the assistant text out of a Responses API response.

    The SDK exposes `response.output_text` as a convenience accessor
    that concatenates all text outputs across blocks. Tolerates test
    doubles that set `output_text` directly OR walk the underlying
    `output[*].content[*].text` tree the same way the SDK does.
    """
    direct = getattr(response, "output_text", None)
    if isinstance(direct, str) and direct:
        return direct
    output = getattr(response, "output", None) or []
    parts: list[str] = []
    for block in output:
        content = getattr(block, "content", None) or []
        for piece in content:
            text = getattr(piece, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)
