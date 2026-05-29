"""OpenAI provider for Flow B (LLM — single-call extraction + structuring)."""

from __future__ import annotations

from awaithumans.providers.base import (
    ExtractionConfig,
    ProviderCredentials,
    StructuringConfig,
)


class OpenAI(ProviderCredentials):
    """OpenAI credentials configured on the AwaitHumans client."""

    api_key: str
    base_url: str | None = None
    organization: str | None = None


class OpenAIExtraction(ExtractionConfig):
    """Single-call Flow B configuration for OpenAI.

    Extracts AND structures in one API call via OpenAI's strict
    JSON-schema response_format. Requires a vision-capable model
    (GPT-4o-2024-08-06 or newer; GPT-5 when available).

    Example:
        OpenAIExtraction(
            model="gpt-5",
            prompt="Extract the codes in column 2 as a JSON list.",
        )
    """

    model: str
    prompt: str
    api_key: str | None = None
    base_url: str | None = None


class OpenAIStructuring(StructuringConfig):
    """OpenAI as the structuring step for an OCR-based extraction.

    Pair with DoclingExtraction / PaddleOCRExtraction / ReductoExtraction
    / AzureDIExtraction to convert raw OCR output into a Pydantic
    response_schema:

        DoclingExtraction(
            structuring=OpenAIStructuring(
                model="gpt-5",
                prompt="Structure this OCR output into the response schema.",
            ),
        )
    """

    api_key: str | None = None
    base_url: str | None = None
