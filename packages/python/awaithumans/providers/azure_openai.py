"""Azure OpenAI deployment for Flow B (LLM — single-call extraction + structuring)."""

from __future__ import annotations

from awaithumans.providers.base import (
    ExtractionConfig,
    ProviderCredentials,
    StructuringConfig,
)


class AzureOpenAI(ProviderCredentials):
    """Azure OpenAI deployment credentials.

    On Azure, the customer creates a deployment of an OpenAI model in
    their Azure portal. The deployment name (not the underlying model
    name) is what you pass as `model=` on AzureOpenAIExtraction.
    """

    api_key: str
    endpoint: str
    api_version: str = "2024-10-21"


class AzureOpenAIExtraction(ExtractionConfig):
    """Single-call Flow B configuration for Azure OpenAI.

    `model` here is the Azure deployment name (configurable per
    deployment in the Azure portal), not the underlying OpenAI
    model id.

    Example:
        AzureOpenAIExtraction(
            model="gpt-5-deployment",
            prompt="Extract the codes in column 2 as a JSON list.",
        )
    """

    model: str
    prompt: str
    api_key: str | None = None
    endpoint: str | None = None
    api_version: str | None = None


class AzureOpenAIStructuring(StructuringConfig):
    """Azure OpenAI as the structuring step for an OCR-based extraction."""

    api_key: str | None = None
    endpoint: str | None = None
    api_version: str | None = None
