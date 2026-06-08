"""AI extractor with per-field confidence, the v2 demo hero step.

Single call to Azure OpenAI (deployment configured by the operator).
Returns field values + per-field confidence + a flat cost estimate. The
confidence map is what drives the hero moment: fields below
DEMO_CONFIDENCE_THRESHOLD route to a human reviewer; high-confidence
fields render immediately as confirmed.

The visitor-facing label remains "AI extractor (GPT-5.5)", the actual
provider deployment is internal. Errors are sanitized through
DemoProviderError so the wizard never leaks Azure-specific details.

The call uses OpenAI's structured outputs path
(``client.responses.parse(text_format=Envelope)``), so the SDK enforces
the schema server-side and returns a real Pydantic instance via
``response.output_parsed``. No JSON-parsing, no schema-rendering, no
post-hoc string coercion. The envelope wraps the schema-builder model
in a ``data`` field plus a ``confidence`` map keyed by top-level field
name.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from awaithumans.server.core.config import settings
from awaithumans.server.services.demo.exceptions import DemoProviderError

if TYPE_CHECKING:
    from openai import AsyncAzureOpenAI

logger = logging.getLogger("awaithumans.server.services.demo.extractor")

# Extraction of a table with 50+ rows on a page can produce 4000-6000
# tokens of structured output. Generous ceiling so a single dense page
# doesn't truncate mid-row and fail the structured-outputs parse.
_MAX_TOKENS = 8000
_COST_PER_CALL_CENTS = 6
_PROVIDER_LABEL = "AI extractor (Azure OpenAI)"

_SYSTEM_PROMPT = (
    "You are an expert document extraction system. Look at the page "
    "image and fill in the structured fields. For every top-level "
    "field, also provide a confidence score 0.0..1.0 in the "
    "`confidence` map: 1.0 means certain, 0.5 means unsure, 0.0 means "
    "total guess. If you cannot read a value confidently, return null "
    "for that field. Be honest about uncertainty: anything you had to "
    "guess should score below 0.85."
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
    argument to responses.parse.
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
    """Tell the model how to populate the structured envelope.

    The schema itself reaches the model via the ``text_format``
    parameter on ``responses.parse``, so this prompt doesn't render the
    shape. It only describes the contract between the ``data`` body
    and the ``confidence`` map.
    """
    return (
        f"Extract a {response_model.__name__} from this page. Fill the `data` "
        "object using the schema you have been given. Populate `confidence` "
        "with one entry per top-level field of `data`. Use null for any "
        "value you cannot read confidently."
    )


async def run_demo_extraction(
    *,
    page_png: bytes,
    response_model: type[BaseModel],
) -> ExtractionResult:
    """Run the configured AI extractor against the page and return values,
    per-field confidences, and a flat per-call cost estimate.

    Uses OpenAI structured outputs: the envelope model is built at call
    time so the schema is bound to the specific ``response_model``
    passed in. The SDK enforces the schema server-side and returns a
    validated Pydantic instance, eliminating the JSON-parsing /
    coercion / re-validation gauntlet the previous implementation ran.
    """
    # Structured outputs require BOTH `data` and `confidence` in the
    # JSON Schema ``required`` array, so neither field can have a
    # default. The envelope also needs ``additionalProperties: false``,
    # which Pydantic emits when ``extra="forbid"`` is set on the model.
    #
    # Azure does NOT allow free-form ``dict[str, float]`` here because
    # structured outputs needs every property named at schema time.
    # We build a sibling confidence model whose fields exactly mirror
    # the top-level fields of ``response_model``, then attach it.
    confidence_fields: dict[str, tuple[Any, Any]] = {
        name: (float, Field(...)) for name in response_model.model_fields
    }
    confidence_model = create_model(
        f"{response_model.__name__}Confidence",
        __config__=ConfigDict(extra="forbid"),
        **confidence_fields,
    )  # type: ignore[call-overload]

    envelope_model = create_model(
        f"{response_model.__name__}Envelope",
        __config__=ConfigDict(extra="forbid"),
        data=(response_model, Field(...)),
        confidence=(confidence_model, Field(...)),
    )

    try:
        client = _build_client()
        image_b64 = base64.standard_b64encode(page_png).decode("ascii")
        response = await client.responses.parse(
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
            text_format=envelope_model,
        )
    except DemoProviderError:
        raise
    except Exception as exc:
        logger.warning("Demo extractor call failed: %s", exc)
        raise DemoProviderError(_PROVIDER_LABEL) from exc

    envelope = response.output_parsed
    if envelope is None:
        logger.warning("Demo extractor returned no parsed envelope.")
        raise DemoProviderError(_PROVIDER_LABEL)

    # `mode="json"` returns nested model instances as plain dicts so the
    # downstream JSON storage (DemoRecord.ai_result, PG JSON column)
    # accepts the dict without a custom serializer.
    values = envelope.data.model_dump(mode="json")
    # ``confidence`` is a typed Pydantic model in production (built
    # above to satisfy structured outputs). Tests sometimes substitute
    # a raw dict directly. Handle both shapes so we don't crash on the
    # test path or on a future SDK that returns the parsed dict.
    raw_confidence_attr = envelope.confidence
    if hasattr(raw_confidence_attr, "model_dump"):
        raw_confidences: dict[str, Any] = raw_confidence_attr.model_dump()
    elif isinstance(raw_confidence_attr, dict):
        raw_confidences = raw_confidence_attr
    else:
        raw_confidences = {}

    confidences: dict[str, float] = {}
    for field_name in values:
        raw = raw_confidences.get(field_name, 0.0)
        try:
            confidences[field_name] = max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            confidences[field_name] = 0.0

    return ExtractionResult(
        values=values,
        confidences=confidences,
        cost_cents=_COST_PER_CALL_CENTS,
    )
