"""PaddleOCR provider for Flow B (open source OCR — needs structuring).

PaddleOCR is the open source OCR engine from PaddlePaddle. It runs
locally on the customer's machine, requires no API key, and emits
raw text with bounding boxes.

Like Docling, the PaddleOCR step does not produce response_schema-
matching output — we pair it with a `StructuringConfig` that turns
the raw OCR output into a Pydantic-matching response.
"""

from __future__ import annotations

from awaithumans.providers.base import ExtractionConfig, StructuringConfig


class PaddleOCRExtraction(ExtractionConfig):
    """Open source OCR via PaddleOCR. No credentials needed.

    Requires the `awaithumans[awaitverify-paddleocr]` extra:

        pip install 'awaithumans[awaitverify-paddleocr]'

    Example:
        PaddleOCRExtraction(
            language="en",
            structuring=OpenAIStructuring(
                model="gpt-5",
                prompt="Structure this OCR output into the response schema.",
            ),
        )
    """

    structuring: StructuringConfig
    language: str = "en"
