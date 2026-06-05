"""Smoke tests for Flow B extraction providers — hit real vendor APIs.

Each test is gated behind the env vars its provider needs and is
skipped silently when those vars are missing. There is no fake mode
here. Purpose: catch regressions where the SDK side of the call
signature drifts away from what current production models accept (the
GPT-5.x Azure Responses-API migration is the canonical case).

Run locally with `pytest tests/awaitverify/test_extraction_smoke.py`
after exporting the relevant credentials. CI does not run these by
default — they cost real tokens and require operator-scoped keys.

Tested model ids:
  - OpenAIExtraction         : gpt-4o-2024-11-20 (default); override via
                               OPENAI_SMOKE_MODEL (e.g., gpt-5).
  - AzureOpenAIExtraction    : the deployment in AZURE_OPENAI_DEPLOYMENT
                               (verified against gpt-5.4 in production).
  - AnthropicExtraction      : claude-sonnet-4-5 (default); override via
                               ANTHROPIC_SMOKE_MODEL (e.g., claude-sonnet-4-6).
  - ReductoExtraction        : Reducto's default extraction pipeline.

Docling, PaddleOCR, AzureDI extraction and Anthropic / AzureOpenAI
structuring are still gated by ProviderNotSupportedError; the tests at
the bottom of this file lock that contract in place."""

from __future__ import annotations

import io
import os
from typing import Final

import pytest
from pydantic import BaseModel, Field

PIL = pytest.importorskip("PIL", reason="Pillow not installed; skip smoke tests")
from PIL import Image, ImageDraw  # noqa: E402

from awaithumans.awaitverify.extraction import (  # noqa: E402
    ProviderNotSupportedError,
    run_extraction,
)
from awaithumans.instance import AwaitHumans  # noqa: E402
from awaithumans.providers import (  # noqa: E402
    Anthropic,
    AnthropicExtraction,
    AnthropicStructuring,
    AzureDIExtraction,
    AzureOpenAI,
    AzureOpenAIExtraction,
    AzureOpenAIStructuring,
    DoclingExtraction,
    OpenAI,
    OpenAIExtraction,
    OpenAIStructuring,
    PaddleOCRExtraction,
    Reducto,
    ReductoExtraction,
)

# ─── tiny test document ────────────────────────────────────────────────


class _Receipt(BaseModel):
    """Minimal extraction target — small enough to keep smoke costs tiny."""

    merchant: str = Field(description="Merchant name as written on the receipt.")
    total: float = Field(description="Total amount in dollars (numeric).")


_RECEIPT_TEXT: Final = "Merchant: BLUE BOTTLE COFFEE\nTotal: $4.75"
_PROMPT: Final = (
    "Extract the merchant name and total dollar amount from this "
    "receipt. Return values that match the schema exactly."
)


def _receipt_png() -> bytes:
    """Render a small, vision-readable PNG receipt."""
    img = Image.new("RGB", (640, 240), "white")
    draw = ImageDraw.Draw(img)
    # Default PIL font is small but legible to current vision models.
    draw.text((20, 60), _RECEIPT_TEXT, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _skip_unless(*env_vars: str) -> None:
    missing = [v for v in env_vars if not os.getenv(v)]
    if missing:
        pytest.skip(f"smoke test requires env vars: {', '.join(missing)}")


def _assert_receipt_extracted(parsed: dict) -> None:
    """Tolerant assertion — vision models vary in capitalization / rounding."""
    record = _Receipt.model_validate(parsed)
    assert "BLUE BOTTLE" in record.merchant.upper(), (
        f"merchant did not include 'BLUE BOTTLE': {record.merchant!r}"
    )
    assert abs(record.total - 4.75) < 0.5, f"total off: {record.total!r}"


# ─── LLM extraction providers ──────────────────────────────────────────


async def test_openai_extraction_smoke() -> None:
    _skip_unless("OPENAI_API_KEY")
    model = os.getenv("OPENAI_SMOKE_MODEL", "gpt-4o-2024-11-20")
    client = AwaitHumans(
        api_key="ah_sk_smoke",
        openai=OpenAI(api_key=os.environ["OPENAI_API_KEY"]),
    )
    parsed = await run_extraction(
        document_bytes=_receipt_png(),
        extraction=OpenAIExtraction(model=model, prompt=_PROMPT),
        response_schema=_Receipt,
        client=client,
    )
    _assert_receipt_extracted(parsed)


async def test_azure_openai_extraction_smoke() -> None:
    """Regression: GPT-5.x Azure deployments rejected response_format on
    chat.completions. The Responses-API path must work without a toggle."""
    _skip_unless(
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
    )
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    client = AwaitHumans(
        api_key="ah_sk_smoke",
        azure_openai=AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_version=api_version,
        ),
    )
    parsed = await run_extraction(
        document_bytes=_receipt_png(),
        extraction=AzureOpenAIExtraction(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            prompt=_PROMPT,
        ),
        response_schema=_Receipt,
        client=client,
    )
    _assert_receipt_extracted(parsed)


async def test_anthropic_extraction_smoke() -> None:
    _skip_unless("ANTHROPIC_API_KEY")
    model = os.getenv("ANTHROPIC_SMOKE_MODEL", "claude-sonnet-4-5")
    client = AwaitHumans(
        api_key="ah_sk_smoke",
        anthropic=Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]),
    )
    parsed = await run_extraction(
        document_bytes=_receipt_png(),
        extraction=AnthropicExtraction(model=model, prompt=_PROMPT),
        response_schema=_Receipt,
        client=client,
    )
    _assert_receipt_extracted(parsed)


async def test_reducto_extraction_smoke() -> None:
    """Reducto's layout pipeline needs more visual structure than our
    synthetic PNG offers. We assert the round-trip succeeds and yields a
    schema-validated dict — not that the values are correct."""
    _skip_unless("REDUCTO_API_KEY")
    model = os.getenv("REDUCTO_SMOKE_MODEL", "reducto-default")
    client = AwaitHumans(
        api_key="ah_sk_smoke",
        reducto=Reducto(api_key=os.environ["REDUCTO_API_KEY"]),
    )
    parsed = await run_extraction(
        document_bytes=_receipt_png(),
        extraction=ReductoExtraction(model=model, prompt=_PROMPT),
        response_schema=_Receipt,
        client=client,
    )
    _Receipt.model_validate(parsed)


# ─── Providers still gated by ProviderNotSupportedError ───────────────


def _stub_client() -> AwaitHumans:
    return AwaitHumans(api_key="ah_sk_smoke")


def _stub_structuring() -> OpenAIStructuring:
    return OpenAIStructuring(model="gpt-4o-2024-11-20", prompt="structure this")


async def test_docling_extraction_raises_not_supported() -> None:
    with pytest.raises(ProviderNotSupportedError):
        await run_extraction(
            document_bytes=_receipt_png(),
            extraction=DoclingExtraction(structuring=_stub_structuring()),
            response_schema=_Receipt,
            client=_stub_client(),
        )


async def test_paddleocr_extraction_raises_not_supported() -> None:
    with pytest.raises(ProviderNotSupportedError):
        await run_extraction(
            document_bytes=_receipt_png(),
            extraction=PaddleOCRExtraction(structuring=_stub_structuring()),
            response_schema=_Receipt,
            client=_stub_client(),
        )


async def test_anthropic_structuring_raises_not_supported() -> None:
    """The structuring dispatcher must reject AnthropicStructuring until
    an implementation lands."""
    from awaithumans.awaitverify.extraction import run_structuring

    with pytest.raises(ProviderNotSupportedError):
        await run_structuring(
            raw_extraction="some OCR text",
            structuring=AnthropicStructuring(model="claude-sonnet-4-5", prompt="structure this"),
            response_schema=_Receipt,
            client=_stub_client(),
        )


# ─── Azure DI (paired with Azure OpenAI structuring) ───────────────────

# Azure DI is paired with AzureOpenAIStructuring here so customers
# whose policy keeps OpenAI usage inside Azure can run the full Flow
# B pipeline without needing an OpenAI direct key. Override the
# structuring deployment via AZURE_OPENAI_STRUCTURING_DEPLOYMENT if it
# differs from AZURE_OPENAI_DEPLOYMENT (rare).


async def test_azure_di_with_azure_structuring_smoke() -> None:
    """Azure Document Intelligence layout → Azure OpenAI structuring."""
    _skip_unless(
        "AZURE_DI_API_KEY",
        "AZURE_DI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
    )
    di_model = os.getenv("AZURE_DI_SMOKE_MODEL", "prebuilt-layout")
    structuring_deployment = os.getenv(
        "AZURE_OPENAI_STRUCTURING_DEPLOYMENT", os.environ["AZURE_OPENAI_DEPLOYMENT"]
    )
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

    from awaithumans.providers import AzureDI

    client = AwaitHumans(
        api_key="ah_sk_smoke",
        azure_di=AzureDI(
            api_key=os.environ["AZURE_DI_API_KEY"],
            endpoint=os.environ["AZURE_DI_ENDPOINT"],
        ),
        azure_openai=AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_version=api_version,
        ),
    )
    parsed = await run_extraction(
        document_bytes=_receipt_png(),
        extraction=AzureDIExtraction(
            model=di_model,
            structuring=AzureOpenAIStructuring(
                model=structuring_deployment,
                prompt=(
                    "Structure the following layout-extracted receipt into "
                    "the response schema. Take the merchant name and total "
                    "from the layout text."
                ),
            ),
        ),
        response_schema=_Receipt,
        client=client,
    )
    _Receipt.model_validate(parsed)
