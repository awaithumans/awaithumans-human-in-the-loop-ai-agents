"""Smoke tests for Flow C verifier providers — hit real vendor APIs.

Each test is gated behind the env vars its provider needs and is
skipped silently when those vars are missing. Purpose: catch
regressions where the SDK side of the call signature drifts away from
what current production models accept (e.g., the gpt-5 Responses-API
migration).

Run locally with `pytest tests/tasks/test_verifier_smoke.py` after
exporting the relevant credentials. CI does not run these by default.

Tested model ids:
  - openai verifier       : gpt-4o-2024-11-20 (default); override via
                            OPENAI_VERIFIER_SMOKE_MODEL (e.g., gpt-5).
  - azure_openai verifier : the deployment in AZURE_OPENAI_DEPLOYMENT
                            (verified against gpt-5.4 in production).
  - claude verifier       : claude-sonnet-4-5 (default); override via
                            CLAUDE_VERIFIER_SMOKE_MODEL.
  - gemini verifier       : gemini-2.5-flash (default); override via
                            GEMINI_VERIFIER_SMOKE_MODEL.

Each test runs ONE verification round and asserts the provider
returned a VerifierResult shaped object (passed/reason fields). The
verifier's verdict (pass vs fail) is informational — model judgments
vary turn-to-turn. We assert the wire-level contract, not the policy."""

from __future__ import annotations

import os

import pytest

from awaithumans.server.verification.providers import (
    azure_openai as azure_provider,
)
from awaithumans.server.verification.providers import (
    claude as claude_provider,
)
from awaithumans.server.verification.providers import (
    gemini as gemini_provider,
)
from awaithumans.server.verification.providers import (
    openai as openai_provider,
)
from awaithumans.types import VerificationContext, VerifierConfig, VerifierResult

# ─── shared fixture data ───────────────────────────────────────────────


def _context() -> VerificationContext:
    """A trivially-correct response so any sane verifier returns passed=True."""
    return VerificationContext(
        task="The user asked: 'Is 2 + 2 equal to 4?'. Approve if yes, reject if no.",
        payload={"question": "Is 2 + 2 equal to 4?"},
        payload_schema={
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
        response={"approved": True},
        response_schema={
            "type": "object",
            "properties": {"approved": {"type": "boolean"}},
            "required": ["approved"],
        },
        raw_input=None,
        attempt=0,
        previous_rejections=[],
    )


_INSTRUCTIONS = (
    "Approve if and only if the user's response correctly answers "
    "the question. Be lenient — this is a smoke test."
)


def _skip_unless(*env_vars: str) -> None:
    missing = [v for v in env_vars if not os.getenv(v)]
    if missing:
        pytest.skip(f"smoke test requires env vars: {', '.join(missing)}")


def _assert_verifier_result(result: object) -> None:
    assert isinstance(result, VerifierResult)
    assert isinstance(result.passed, bool)
    assert isinstance(result.reason, str)


# ─── OpenAI ────────────────────────────────────────────────────────────


async def test_openai_verifier_smoke() -> None:
    _skip_unless("OPENAI_API_KEY")
    model = os.getenv("OPENAI_VERIFIER_SMOKE_MODEL", "gpt-4o-2024-11-20")
    config = VerifierConfig(
        provider="openai",
        model=model,
        instructions=_INSTRUCTIONS,
        api_key_env="OPENAI_API_KEY",
    )
    result = await openai_provider.verify(config, _context())
    _assert_verifier_result(result)


# ─── Azure OpenAI ──────────────────────────────────────────────────────


async def test_azure_openai_verifier_smoke() -> None:
    """Regression: GPT-5.x Azure deployments rejected the chat.completions
    + response_format path. The Responses-API verifier must work."""
    _skip_unless(
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
    )
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    config = VerifierConfig(
        provider="azure_openai",
        model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        instructions=_INSTRUCTIONS,
        api_key_env="AZURE_OPENAI_API_KEY",
        metadata={
            "endpoint_env": "AZURE_OPENAI_ENDPOINT",
            "api_version": api_version,
            "deployment": os.environ["AZURE_OPENAI_DEPLOYMENT"],
        },
    )
    result = await azure_provider.verify(config, _context())
    _assert_verifier_result(result)


# ─── Claude ────────────────────────────────────────────────────────────


async def test_claude_verifier_smoke() -> None:
    _skip_unless("ANTHROPIC_API_KEY")
    model = os.getenv("CLAUDE_VERIFIER_SMOKE_MODEL", "claude-sonnet-4-5")
    config = VerifierConfig(
        provider="claude",
        model=model,
        instructions=_INSTRUCTIONS,
        api_key_env="ANTHROPIC_API_KEY",
    )
    result = await claude_provider.verify(config, _context())
    _assert_verifier_result(result)


# ─── Gemini ────────────────────────────────────────────────────────────


async def test_gemini_verifier_smoke() -> None:
    _skip_unless("GEMINI_API_KEY")
    model = os.getenv("GEMINI_VERIFIER_SMOKE_MODEL", "gemini-2.5-flash")
    config = VerifierConfig(
        provider="gemini",
        model=model,
        instructions=_INSTRUCTIONS,
        api_key_env="GEMINI_API_KEY",
    )
    result = await gemini_provider.verify(config, _context())
    _assert_verifier_result(result)
