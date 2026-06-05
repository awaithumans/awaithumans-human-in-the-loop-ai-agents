"""Azure OpenAI verifier.

Uses the Responses API (`client.responses.create`) with a JSON-schema
text.format. Required for GPT-5.x Azure deployments, which reject the
older chat.completions + response_format path: 'Unsupported parameter:
response_format. In the Responses API, this parameter has moved to
text.format.'

Reads three things from VerifierConfig.metadata:
  - endpoint_env  (default: AZURE_OPENAI_ENDPOINT) — full base URL
  - api_version   (default: 2024-10-21)
  - deployment    — Azure deployment name (required; goes in `model` slot)

`config.api_key_env` defaults to `AZURE_OPENAI_API_KEY`.
"""

from __future__ import annotations

import json

from awaithumans.server.core.config import settings
from awaithumans.server.services.exceptions import (
    VerifierAPIKeyMissingError,
    VerifierEndpointMissingError,
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
    VERIFIER_AZURE_DEFAULT_API_KEY_ENV,
    VERIFIER_AZURE_DEFAULT_API_VERSION,
    VERIFIER_AZURE_DEFAULT_ENDPOINT_ENV,
    VERIFIER_MAX_OUTPUT_TOKENS,
    VERIFIER_OUTPUT_SCHEMA_NAME,
)


async def verify(config: VerifierConfig, ctx: VerificationContext) -> VerifierResult:
    try:
        from openai import AsyncAzureOpenAI
    except ImportError as exc:
        raise VerifierProviderUnavailableError("azure", "verifier-azure") from exc

    api_key_env = config.api_key_env or VERIFIER_AZURE_DEFAULT_API_KEY_ENV
    api_key = settings.get_secret(api_key_env)
    if not api_key:
        raise VerifierAPIKeyMissingError(api_key_env)

    metadata = config.metadata or {}
    endpoint_env = metadata.get("endpoint_env", VERIFIER_AZURE_DEFAULT_ENDPOINT_ENV)
    endpoint = settings.get_secret(endpoint_env)
    if not endpoint:
        raise VerifierEndpointMissingError(endpoint_env)

    api_version = metadata.get("api_version", VERIFIER_AZURE_DEFAULT_API_VERSION)
    deployment = metadata.get("deployment") or config.model
    if not deployment:
        raise VerifierProviderError(
            "azure",
            "Azure OpenAI requires a deployment name. Set it in "
            "VerifierConfig.metadata['deployment'] or .model.",
        )

    client = AsyncAzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
    )

    # See providers/openai.py for the strict=False rationale: the
    # widened `parsed_response.type` union can't satisfy Responses-API
    # nested-strict rules. Wire shape is still enforced by
    # VERIFIER_OUTPUT_SCHEMA and `_to_result`.
    strict_schema = to_openai_strict_schema(VERIFIER_OUTPUT_SCHEMA)

    try:
        response = await client.responses.create(
            model=deployment,  # for Azure, "model" is the deployment name
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
        raise VerifierProviderError("azure", sanitize_provider_error_detail(str(exc))) from exc

    content = response.output_text
    if not content:
        raise VerifierProviderError("azure", "Empty response content.")

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise VerifierProviderError("azure", f"Response was not valid JSON: {exc.msg}") from exc

    return VerifierResult(
        passed=bool(payload.get("passed", False)),
        reason=str(payload.get("reason", "")),
        parsed_response=payload.get("parsed_response"),
    )
