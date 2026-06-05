"""AI extractor with per-field confidence, the v2 demo hero step.

Single call to Azure OpenAI (deployment configured by the operator).
Returns field values + per-field confidence + a flat cost estimate. The
confidence map is what drives the hero moment: fields below
DEMO_CONFIDENCE_THRESHOLD route to a human reviewer; high-confidence
fields render immediately as confirmed.

The visitor-facing label remains "AI extractor (GPT-5.5)" — the actual
provider deployment is internal. Errors are sanitized through
DemoProviderError so the wizard never leaks Azure-specific details.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from awaithumans.server.core.config import settings
from awaithumans.server.services.demo.exceptions import DemoProviderError

if TYPE_CHECKING:
    from openai import AsyncAzureOpenAI

logger = logging.getLogger("awaithumans.server.services.demo.extractor")

_MAX_TOKENS = 2000
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
    "ambiguous should drop below 0.85. Return JSON only, no prose."
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
    field_descriptions: list[str] = []
    for name, field in response_model.model_fields.items():
        annotation = field.annotation
        py_type = getattr(annotation, "__name__", None) or str(annotation)
        field_descriptions.append(f'  "{name}": <{py_type}>')
    schema_block = "{\n" + ",\n".join(field_descriptions) + "\n}"

    return (
        "Extract these fields from the page image and return JSON of the form "
        '{"data": {...}, "confidence": {...}}. The data object must match this '
        f"schema exactly:\n\n{schema_block}\n\n"
        "The confidence object maps each field name to a 0.0-1.0 score."
    )


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
        # In Azure mode the OpenAI SDK accepts the DEPLOYMENT name as
        # the `model` argument and dispatches to that deployment under
        # the hood. `response_format={"type": "json_object"}` enables
        # JSON mode so the model is forced to return parseable JSON.
        response = await client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT or "",
            max_tokens=_MAX_TOKENS,
            response_format={"type": "json_object"},
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
                        {
                            "type": "text",
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

    values = validated.model_dump()
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
    """Pull the assistant text out of a ChatCompletion response.

    Tolerates both the real OpenAI SDK shape (`response.choices[0]
    .message.content`) and minimal SimpleNamespace test doubles. Returns
    an empty string when the response is malformed; the JSON parse step
    above will then surface a clean DemoProviderError to the caller.
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
