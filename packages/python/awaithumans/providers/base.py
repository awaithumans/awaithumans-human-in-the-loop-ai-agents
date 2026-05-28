"""Base classes for provider credentials, extraction configs, and structuring configs.

Three roles a provider can play in Flow B:

  ProviderCredentials       — what you pass to AwaitHumans on init.
                              Holds api_key, base_url, endpoint, etc.
                              Configured once, used many times.

  ExtractionConfig          — what you pass to verify_document per call.
                              Describes how to turn the document into
                              structured data. Two flavors:

      LLM ExtractionConfig: single-call extraction + structuring
          (OpenAI, Anthropic, AzureOpenAI). No `structuring` field.
          The provider's structured-output API emits the Pydantic
          response_schema directly.

      OCR ExtractionConfig: two-step pipeline — extraction first,
          structuring second. Required `structuring` field naming
          which LLM to use for the second step. Providers in this
          category: Docling, PaddleOCR, Reducto, Azure DI.

  StructuringConfig         — used only inside OCR ExtractionConfig
                              subclasses. Names the LLM that turns
                              raw OCR output into the response_schema.
                              Same provider classes are available
                              (OpenAIStructuring, AnthropicStructuring,
                              AzureOpenAIStructuring).
"""

from __future__ import annotations

from pydantic import BaseModel


class ProviderCredentials(BaseModel):
    """Base for provider credentials set on the AwaitHumans client.

    The customer's API key NEVER leaves the customer's environment —
    the SDK uses it locally to call the customer's chosen model.
    """


class ExtractionConfig(BaseModel):
    """Base for per-call Flow B extraction configuration.

    Subclasses fall into two flavors (see module docstring).
    """


class StructuringConfig(BaseModel):
    """Base for the structuring step in OCR-based extraction pipelines.

    OCR providers (Docling, PaddleOCR, Reducto, Azure DI) extract raw
    layout / text but cannot natively emit a Pydantic-matching
    response. They require a structuring LLM (OpenAIStructuring,
    AnthropicStructuring, AzureOpenAIStructuring) that takes the raw
    output plus the response_schema and emits structured JSON.
    """

    prompt: str
    model: str
