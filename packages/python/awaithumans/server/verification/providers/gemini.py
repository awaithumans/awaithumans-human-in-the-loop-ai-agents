"""Google Gemini verifier.

Uses Gemini's `response_schema` (structured output) to force the model
to fill VERIFIER_OUTPUT_SCHEMA. The `google-generativeai` SDK is sync;
we run it in a thread executor to keep the FastAPI handler async.

The vendor SDK uses module-level state for the API key
(`genai.configure(api_key=...)` mutates a global). Two concurrent
verifications using *different* keys would race — request N could read
request N+1's key. We serialise configure() + GenerativeModel() under a
single lock to keep the configure → model → call sequence atomic per
process. Operators with multiple distinct Gemini keys should use
OpenAI/Claude verifiers; this is a single-tenant SDK constraint until
the `google-genai` SDK gets adopted (per-call clients)."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

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
)
from awaithumans.server.verification.providers import sanitize_provider_error_detail
from awaithumans.types import VerificationContext, VerifierConfig, VerifierResult
from awaithumans.utils.constants import (
    VERIFIER_GEMINI_DEFAULT_API_KEY_ENV,
    VERIFIER_GEMINI_DEFAULT_MODEL,
    VERIFIER_MAX_OUTPUT_TOKENS,
)

# Serialises the configure() + GenerativeModel() + generate_content()
# sequence so two concurrent calls with different api_keys can't read
# each other's globals. The actual generate_content() runs inside the
# lock too, which means Gemini calls don't parallelise on a single
# server — accept it for v0.1; real fix is the `google-genai` SDK.
_GEMINI_GLOBAL_LOCK = threading.Lock()


async def verify(config: VerifierConfig, ctx: VerificationContext) -> VerifierResult:
    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise VerifierProviderUnavailableError("gemini", "verifier-gemini") from exc

    api_key_env = config.api_key_env or VERIFIER_GEMINI_DEFAULT_API_KEY_ENV
    api_key = settings.get_secret(api_key_env)
    if not api_key:
        raise VerifierAPIKeyMissingError(api_key_env)

    model_name = config.model or VERIFIER_GEMINI_DEFAULT_MODEL
    system_prompt = build_system_prompt(config.instructions)
    user_prompt = build_user_prompt(ctx)

    def _call_sync() -> dict[str, Any]:
        with _GEMINI_GLOBAL_LOCK:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_prompt,
            )
            result = model.generate_content(
                user_prompt,
                generation_config={
                    "response_mime_type": "application/json",
                    "response_schema": _to_gemini_schema(VERIFIER_OUTPUT_SCHEMA),
                    "max_output_tokens": VERIFIER_MAX_OUTPUT_TOKENS,
                },
            )
            return {"text": result.text}

    try:
        response = await asyncio.to_thread(_call_sync)
    except Exception as exc:  # noqa: BLE001
        raise VerifierProviderError("gemini", sanitize_provider_error_detail(str(exc))) from exc

    text = response.get("text") or ""
    if not text:
        raise VerifierProviderError("gemini", "Empty response content.")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VerifierProviderError("gemini", f"Response was not valid JSON: {exc.msg}") from exc

    return VerifierResult(
        passed=bool(payload.get("passed", False)),
        reason=str(payload.get("reason", "")),
        parsed_response=payload.get("parsed_response"),
    )


def _to_gemini_schema(schema: dict) -> dict:
    """Convert OpenAPI-ish schema to Gemini's expected shape.

    Gemini accepts a subset of OpenAPI schemas; we strip schema
    keywords it doesn't grok so it doesn't 400 on unknown fields.
    Crucially, property NAMES (the keys inside `properties` and the
    string entries in `required`) are content, not keywords — they
    must be preserved verbatim."""
    return _strip_schema_keywords(schema)


_SCHEMA_KEYWORDS_GEMINI_KEEPS: frozenset[str] = frozenset(
    {"type", "properties", "required", "description", "items", "enum"}
)


def _strip_schema_keywords(node: Any) -> Any:
    """Recursively drop schema keywords Gemini doesn't accept.

    `properties` is a name → subschema map: filter the SUBSCHEMAS but
    keep every name. Everywhere else (top-level, items, anyOf, etc.)
    we filter keys against the allowlist."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k not in _SCHEMA_KEYWORDS_GEMINI_KEEPS:
                continue
            if k == "properties" and isinstance(v, dict):
                out[k] = {name: _strip_schema_keywords(sub) for name, sub in v.items()}
            elif k == "required" and isinstance(v, list):
                # Property-name list — strings, not subschemas.
                out[k] = list(v)
            else:
                out[k] = _strip_schema_keywords(v)
        return out
    if isinstance(node, list):
        return [_strip_schema_keywords(v) for v in node]
    return node
