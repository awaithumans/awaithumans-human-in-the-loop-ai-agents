"""Docling provider for Flow B (open source OCR + layout — needs structuring).

Docling is IBM's open source document conversion library. It runs
locally on the customer's machine, requires no API key, and emits
structured layout JSON.

The Docling extraction step alone does not produce response_schema-
matching output — we pair it with a `StructuringConfig` (OpenAI,
Anthropic, AzureOpenAI) that turns Docling's layout JSON into a
Pydantic-matching response.
"""

from __future__ import annotations

from awaithumans.providers.base import ExtractionConfig, StructuringConfig


class DoclingExtraction(ExtractionConfig):
    """Open source OCR + layout via Docling. No credentials needed.

    Requires the `awaithumans[awaitverify-docling]` extra:

        pip install 'awaithumans[awaitverify-docling]'

    Example:
        DoclingExtraction(
            structuring=OpenAIStructuring(
                model="gpt-5",
                prompt="Structure this layout into the response schema.",
            ),
        )
    """

    structuring: StructuringConfig

    # Docling does not take prompts or model selection in the same
    # sense as LLM providers. These fields are inherited from the
    # base for uniform metadata but are not configurable here in v1.
    # Subclassing may add Docling-specific knobs (PDF backend, OCR
    # engine, etc.) in a future revision.
