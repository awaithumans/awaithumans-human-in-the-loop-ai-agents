"""Typed provider configurations for AwaitVerify Flow B.

Two flavors of provider:

  LLM providers (single-call extraction + structuring):
      OpenAI / OpenAIExtraction / OpenAIStructuring
      Anthropic / AnthropicExtraction / AnthropicStructuring
      AzureOpenAI / AzureOpenAIExtraction / AzureOpenAIStructuring

  OCR providers (raw extraction → required structuring step):
      Docling / DoclingExtraction              (open source, no key)
      PaddleOCR / PaddleOCRExtraction          (open source, no key)
      Reducto / ReductoExtraction              (SaaS, BYOK)
      AzureDI / AzureDIExtraction              (SaaS, BYOK)

Init pattern:

    from awaithumans import AwaitHumans
    from awaithumans.providers import OpenAI, Anthropic

    client = AwaitHumans(
        api_key="ah_sk_...",
        openai=OpenAI(api_key="sk-..."),
        anthropic=Anthropic(api_key="sk-ant-..."),
    )

LLM extraction:

    extraction=OpenAIExtraction(model="gpt-5", prompt="Extract codes.")

OCR extraction (always paired with a structuring config):

    extraction=DoclingExtraction(
        structuring=OpenAIStructuring(
            model="gpt-5",
            prompt="Structure this layout into the response schema.",
        ),
    )
"""

from __future__ import annotations

from awaithumans.providers.anthropic import (
    Anthropic,
    AnthropicExtraction,
    AnthropicStructuring,
)
from awaithumans.providers.azure_di import AzureDI, AzureDIExtraction
from awaithumans.providers.azure_openai import (
    AzureOpenAI,
    AzureOpenAIExtraction,
    AzureOpenAIStructuring,
)
from awaithumans.providers.base import (
    ExtractionConfig,
    ProviderCredentials,
    StructuringConfig,
)
from awaithumans.providers.docling import DoclingExtraction
from awaithumans.providers.openai import (
    OpenAI,
    OpenAIExtraction,
    OpenAIStructuring,
)
from awaithumans.providers.paddle_ocr import PaddleOCRExtraction
from awaithumans.providers.reducto import Reducto, ReductoExtraction

__all__ = [
    # base classes
    "ProviderCredentials",
    "ExtractionConfig",
    "StructuringConfig",
    # OpenAI
    "OpenAI",
    "OpenAIExtraction",
    "OpenAIStructuring",
    # Anthropic
    "Anthropic",
    "AnthropicExtraction",
    "AnthropicStructuring",
    # Azure OpenAI
    "AzureOpenAI",
    "AzureOpenAIExtraction",
    "AzureOpenAIStructuring",
    # OCR providers
    "DoclingExtraction",
    "PaddleOCRExtraction",
    "Reducto",
    "ReductoExtraction",
    "AzureDI",
    "AzureDIExtraction",
]
