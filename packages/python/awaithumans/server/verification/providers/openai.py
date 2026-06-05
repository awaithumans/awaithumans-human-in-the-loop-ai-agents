"""OpenAI verifier.

Uses the Responses API (`client.responses.create`) with a JSON-schema
text.format to force the model to fill VERIFIER_OUTPUT_SCHEMA. The
older chat.completions + response_format path was removed because
gpt-5 rejects it: 'Unsupported parameter: response_format. In the
Responses API, this parameter has moved to text.format.'"""

from __future__ import annotations

import json

from awaithumans.server.core.config import settings
from awaithumans.server.services.exceptions import (
    VerifierAPIKeyMissingError,
    VerifierProviderError,
    VerifierProviderUnavailableError,
)
from awaithumans.server.verification.prompt import (
    VERIFIER_OUTPUT_SCHEMA,
    build_system_prompt,
    build_user_prompt,
    to_openai_strict_schema,
)
from awaithumans.server.verification.providers import sanitize_provider_error_detail
from awaithumans.types import VerificationContext, VerifierConfig, VerifierResult
from awaithumans.utils.constants import (
    VERIFIER_MAX_OUTPUT_TOKENS,
    VERIFIER_OPENAI_DEFAULT_API_KEY_ENV,
    VERIFIER_OPENAI_DEFAULT_MODEL,
    VERIFIER_OUTPUT_SCHEMA_NAME,
)


async def verify(config: VerifierConfig, ctx: VerificationContext) -> VerifierResult:
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise VerifierProviderUnavailableError("openai", "verifier-openai") from exc

    api_key_env = config.api_key_env or VERIFIER_OPENAI_DEFAULT_API_KEY_ENV
    api_key = settings.get_secret(api_key_env)
    if not api_key:
        raise VerifierAPIKeyMissingError(api_key_env)

    client = AsyncOpenAI(api_key=api_key)
    model = config.model or VERIFIER_OPENAI_DEFAULT_MODEL

    # `to_openai_strict_schema()` widens `parsed_response.type` into a
    # union (object | string | number | boolean | array | null) because
    # the field's actual shape depends on each task's response_schema
    # and isn't known here. Responses-API strict mode (unlike the
    # older chat.completions strict mode) walks nested type unions and
    # demands additionalProperties:false on every object branch / items
    # on every array branch — which we can't satisfy without knowing
    # the task schema. We send strict=False; the wire-level shape is
    # still enforced by VERIFIER_OUTPUT_SCHEMA and `_to_result` below.
    strict_schema = to_openai_strict_schema(VERIFIER_OUTPUT_SCHEMA)

    try:
        response = await client.responses.create(
            model=model,
            instructions=build_system_prompt(config.instructions),
            input=build_user_prompt(ctx),
            text={
                "format": {
                    "type": "json_schema",
                    "name": VERIFIER_OUTPUT_SCHEMA_NAME,
                    "schema": strict_schema,
                    "strict": False,
                }
            },
            max_output_tokens=VERIFIER_MAX_OUTPUT_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001
        raise VerifierProviderError("openai", sanitize_provider_error_detail(str(exc))) from exc

    content = response.output_text
    if not content:
        raise VerifierProviderError("openai", "Empty response content.")

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise VerifierProviderError("openai", f"Response was not valid JSON: {exc.msg}") from exc

    return VerifierResult(
        passed=bool(payload.get("passed", False)),
        reason=str(payload.get("reason", "")),
        parsed_response=payload.get("parsed_response"),
    )
