"""Anthropic (Claude) provider for Flow B (LLM — single-call extraction + structuring)."""

from __future__ import annotations

from awaithumans.providers.base import (
    ExtractionConfig,
    ProviderCredentials,
    StructuringConfig,
)


class Anthropic(ProviderCredentials):
    """Anthropic credentials configured on the AwaitHumans client."""

    api_key: str
    base_url: str | None = None


class AnthropicExtraction(ExtractionConfig):
    """Single-call Flow B configuration for Anthropic Claude.

    Uses Claude's tool-use API to force structured output matching
    the response_schema. Requires a vision-capable model
    (claude-sonnet-4-5 or newer).

    Example:
        AnthropicExtraction(
            model="claude-sonnet-4-5",
            prompt="Extract the codes in column 2 as a JSON list.",
        )
    """

    model: str
    prompt: str
    api_key: str | None = None
    base_url: str | None = None


class AnthropicStructuring(StructuringConfig):
    """Claude as the structuring step for an OCR-based extraction."""

    api_key: str | None = None
    base_url: str | None = None
