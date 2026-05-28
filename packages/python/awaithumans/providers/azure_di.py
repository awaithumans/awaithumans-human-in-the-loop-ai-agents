"""Azure Document Intelligence provider for Flow B (closed-source — needs structuring).

Azure DI (formerly Form Recognizer) is Microsoft's document AI service.
Customers bring their own Azure DI endpoint + API key; the SDK calls
Azure DI from the customer's machine.

Azure DI returns a typed AnalyzedDocument (paragraphs, tables, key-
value pairs) that does not match the customer's response_schema, so
it requires a structuring step.
"""

from __future__ import annotations

from awaithumans.providers.base import (
    ExtractionConfig,
    ProviderCredentials,
    StructuringConfig,
)


class AzureDI(ProviderCredentials):
    """Azure Document Intelligence credentials."""

    api_key: str
    endpoint: str


class AzureDIExtraction(ExtractionConfig):
    """Azure Document Intelligence. Requires endpoint + key + structuring.

    `model` is the Azure DI model identifier (e.g., "prebuilt-layout",
    "prebuilt-document", a custom-trained model id, etc.).

    Requires the `awaithumans[awaitverify-azure-di]` extra:

        pip install 'awaithumans[awaitverify-azure-di]'

    Example:
        AzureDIExtraction(
            model="prebuilt-layout",
            structuring=OpenAIStructuring(
                model="gpt-5",
                prompt="Structure this analyzed document into the response schema.",
            ),
        )
    """

    structuring: StructuringConfig
    model: str = "prebuilt-layout"
    api_key: str | None = None
    endpoint: str | None = None
