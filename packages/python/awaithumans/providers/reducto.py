"""Reducto provider for Flow B (LLM-style — single-call structured extraction).

Reducto's `/extract` endpoint accepts a schema and returns structured
output matching it. Like OpenAI / Claude / Azure OpenAI, this is a
single-call provider — no separate structuring step.

See docs.reducto.ai/extract/overview for the endpoint contract.

The SDK calls Reducto from the customer's machine using their API
key. The document is sent to Reducto (their choice when picking this
provider), and the structured result flows back to AwaitVerify as
the input to the human review step.
"""

from __future__ import annotations

from awaithumans.providers.base import ExtractionConfig, ProviderCredentials


class Reducto(ProviderCredentials):
    """Reducto credentials configured on the AwaitHumans client.

    Example:
        client = AwaitHumans(
            api_key="ah_sk_...",
            reducto=Reducto(api_key="red-..."),
        )
    """

    api_key: str
    base_url: str | None = None


class ReductoExtraction(ExtractionConfig):
    """Single-call Flow B configuration for Reducto's `/extract`.

    Reducto accepts the response_schema and returns structured output
    matching it. No separate structuring step required.

    Requires the `awaithumans[awaitverify-reducto]` extra:

        pip install 'awaithumans[awaitverify-reducto]'

    Example:
        ReductoExtraction(
            model="reducto-extract-default",
            prompt="Extract the codes in column 2 of the hand-drawn table.",
        )

    The `prompt` is passed as Reducto's extraction instructions. The
    `model` selects the Reducto extraction model (typically left at
    the default unless the customer has a custom model).
    """

    model: str = "reducto-extract-default"
    prompt: str
    api_key: str | None = None
    base_url: str | None = None
