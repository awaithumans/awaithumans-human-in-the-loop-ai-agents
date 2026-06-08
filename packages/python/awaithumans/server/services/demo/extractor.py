"""AI extractor with per-field confidence, the v2 demo hero step.

Single call to Azure OpenAI (deployment configured by the operator).
Returns field values + per-field confidence + a flat cost estimate. The
confidence map is what drives the hero moment: fields below
DEMO_CONFIDENCE_THRESHOLD route to a human reviewer; high-confidence
fields render immediately as confirmed.

The visitor-facing label remains "AI extractor (GPT-5.5)", the actual
provider deployment is internal. Errors are sanitized through
DemoProviderError so the wizard never leaks Azure-specific details.

Nested ``record`` and ``list[record]`` schemas (added in the
schema_builder revision) render as Python-class-like blocks in the
user prompt so the model sees the full structure rather than a flat
``{"field": "type"}`` map. Confidence is still reported at the
top-level field name only, so a flagged ``employees`` field covers
the whole list rather than per-row dialogue.
"""

from __future__ import annotations

import base64
import json
import logging
import typing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from awaithumans.server.core.config import settings
from awaithumans.server.services.demo.exceptions import DemoProviderError

if TYPE_CHECKING:
    from openai import AsyncAzureOpenAI

logger = logging.getLogger("awaithumans.server.services.demo.extractor")

# Extraction of a table with 50+ rows on a page can produce 4000-6000
# tokens of JSON. Generous ceiling so a single dense page doesn't
# truncate mid-row and trip the JSON parser.
_MAX_TOKENS = 8000
_COST_PER_CALL_CENTS = 6
_PROVIDER_LABEL = "AI extractor (Azure OpenAI)"

_SYSTEM_PROMPT = (
    "You are an expert document extraction system. Given an image of a "
    "page from a document and a JSON schema, extract the requested "
    "fields exactly as they appear on the page. For every field, also "
    "rate your confidence on a 0.0-1.0 scale: 1.0 means certain, "
    "0.5 means roughly half-certain, 0.0 means total guess. Be honest "
    "about uncertainty. Fields you can read clearly should be 0.9 or "
    "higher; fields where the image is blurry or the value is "
    "ambiguous should drop below 0.85. "
    "Return ONLY a JSON object. No prose, no markdown, no code fences."
)


@dataclass(frozen=True)
class ExtractionResult:
    values: dict[str, Any]
    confidences: dict[str, float]
    cost_cents: int


def _build_client() -> AsyncAzureOpenAI:
    """Construct an Azure OpenAI client from configured settings.

    All three of endpoint + deployment + api_key must be set. Missing
    any one of them surfaces as `DemoProviderError` so the wizard shows
    a generic "extractor unavailable" message rather than a stack
    trace. The deployment name is consumed downstream as the `model`
    argument to chat.completions.create.
    """
    try:
        from openai import AsyncAzureOpenAI  # noqa: PLC0415
    except ImportError as exc:
        logger.warning("OpenAI SDK not installed: %s", exc)
        raise DemoProviderError(_PROVIDER_LABEL) from exc

    if not (
        settings.AZURE_OPENAI_API_KEY
        and settings.AZURE_OPENAI_ENDPOINT
        and settings.AZURE_OPENAI_DEPLOYMENT
    ):
        raise DemoProviderError(_PROVIDER_LABEL)

    return AsyncAzureOpenAI(
        api_key=settings.AZURE_OPENAI_API_KEY,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_version=settings.AZURE_OPENAI_API_VERSION,
    )


def _build_user_prompt(response_model: type[BaseModel]) -> str:
    schema_block = _render_model(response_model, indent=0)
    return (
        "Extract these fields from the page image and return JSON of the form "
        '{"data": {...}, "confidence": {...}}. The data object must match this '
        f"schema exactly:\n\n{schema_block}\n\n"
        "The confidence object maps each TOP-LEVEL field name to a 0.0-1.0 score. "
        "For nested records and lists of records, score the whole field as a unit "
        "rather than scoring each nested sub-field."
    )


def _render_model(model: type[BaseModel], *, indent: int) -> str:
    """Render a Pydantic model as a Python-class-like schema block.

    Walks ``model.model_fields`` and emits one line per field. Nested
    Pydantic models render as ``Name { ... }`` with their own fields
    indented. ``list[SomeModel]`` renders as
    ``list[record { ... }]``. Primitives render as their
    ``__name__``.
    """
    pad = "  " * indent
    inner_pad = "  " * (indent + 1)
    lines = [f"{pad}{model.__name__} {{"]
    for name, field in model.model_fields.items():
        rendered = _render_annotation(field.annotation, indent=indent + 1)
        lines.append(f"{inner_pad}{name}: {rendered}")
    lines.append(f"{pad}}}")
    return "\n".join(lines)


def _render_annotation(annotation: Any, *, indent: int) -> str:
    """Render a single field annotation as a human-readable type string.

    Three cases:
      1. BaseModel subclass: emit ``Name { ... }`` with nested fields
         indented one level deeper.
      2. ``list[SomeModel]``: emit ``list[record { ... }]`` so the LLM
         sees the row shape inline.
      3. Anything else: fall back to ``__name__`` or ``str(annotation)``
         so primitives render as ``str``, ``int``, ``list[str]``, etc.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _render_model(annotation, indent=indent).lstrip()

    origin = typing.get_origin(annotation)
    if origin is list:
        (inner,) = typing.get_args(annotation) or (None,)
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            nested = _render_model(inner, indent=indent).lstrip()
            # Strip the leading "Name " so the reader sees
            # ``list[record { ... }]`` rather than ``list[Foo { ... }]``;
            # the row's own class name is unhelpful noise here.
            body = nested[nested.index("{") :]
            return f"list[record {body}]"

    name = getattr(annotation, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return str(annotation)


async def run_demo_extraction(
    *,
    page_png: bytes,
    response_model: type[BaseModel],
) -> ExtractionResult:
    """Run the configured AI extractor against the page and return values,
    per-field confidences, and a flat per-call cost estimate."""
    try:
        client = _build_client()
        image_b64 = base64.standard_b64encode(page_png).decode("ascii")
        # Azure's Responses API takes `input` (not `messages`),
        # `max_output_tokens` (not `max_tokens`), and content types
        # `input_text` / `input_image`. The system prompt enforces JSON.
        response = await client.responses.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT or "",
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
                        {
                            "type": "input_text",
                            "text": _build_user_prompt(response_model),
                        },
                    ],
                },
            ],
        )
    except DemoProviderError:
        raise
    except Exception as exc:
        logger.warning("Demo extractor call failed: %s", exc)
        raise DemoProviderError(_PROVIDER_LABEL) from exc

    raw_text = _extract_text(response)
    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Demo extractor returned non-JSON: %r", raw_text[:200])
        raise DemoProviderError(_PROVIDER_LABEL) from exc

    if not isinstance(parsed, dict) or "data" not in parsed:
        logger.warning("Demo extractor JSON missing 'data': %r", parsed)
        raise DemoProviderError(_PROVIDER_LABEL)

    raw_values = parsed["data"]
    raw_confidences = parsed.get("confidence", {})

    try:
        validated = response_model.model_validate(raw_values)
    except ValidationError as exc:
        logger.warning("Demo extractor schema mismatch: %s", exc)
        raise DemoProviderError(_PROVIDER_LABEL) from exc

    # `mode="json"` returns date/datetime as ISO strings so the
    # downstream JSON storage (DemoRecord.ai_result, PG JSON column)
    # accepts the dict without a custom serializer.
    values = validated.model_dump(mode="json")
    confidences: dict[str, float] = {}
    raw_confidences_dict = raw_confidences if isinstance(raw_confidences, dict) else {}
    for field_name in values:
        raw = raw_confidences_dict.get(field_name, 0.0)
        try:
            confidences[field_name] = max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            confidences[field_name] = 0.0

    return ExtractionResult(
        values=values,
        confidences=confidences,
        cost_cents=_COST_PER_CALL_CENTS,
    )


def _extract_text(response: Any) -> str:
    """Pull the assistant text out of a Responses API response.

    Prefers the SDK's `output_text` convenience accessor; falls back to
    walking `output[*].content[*].text` for test doubles that don't
    populate the shortcut. Returns "" on malformed responses; the JSON
    parse step above then raises a clean DemoProviderError.
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
