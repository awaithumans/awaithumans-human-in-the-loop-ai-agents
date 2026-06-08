from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel, Field, create_model

from awaithumans.server.services.demo.exceptions import DemoProviderError
from awaithumans.server.services.demo.extractor import (
    ExtractionResult,
    run_demo_extraction,
)


class _Receipt(BaseModel):
    vendor: str | None = None
    total_cents: int | None = None


def _make_envelope(data: _Receipt, confidence: dict[str, float]) -> BaseModel:
    """Build the envelope shape the production code requests.

    Mirrors the dynamic envelope the extractor builds at call time so
    the mocked ``output_parsed`` matches the real SDK contract.
    """
    Envelope = create_model(  # noqa: N806 -- dynamically created class
        "_ReceiptEnvelope",
        data=(_Receipt, Field(...)),
        confidence=(dict[str, float], Field(default_factory=dict)),
    )
    return Envelope(data=data, confidence=confidence)


@pytest.mark.asyncio
async def test_happy_path() -> None:
    fake_envelope = _make_envelope(
        data=_Receipt(vendor="Acme Corp", total_cents=1299),
        confidence={"vendor": 0.97, "total_cents": 0.62},
    )
    fake_response = SimpleNamespace(output_parsed=fake_envelope)

    with patch(
        "awaithumans.server.services.demo.extractor._build_client"
    ) as mock_client_builder:
        client = AsyncMock()
        client.responses.parse = AsyncMock(return_value=fake_response)
        mock_client_builder.return_value = client

        result = await run_demo_extraction(
            page_png=b"\x89PNG fake",
            response_model=_Receipt,
        )

    assert isinstance(result, ExtractionResult)
    assert result.values == {"vendor": "Acme Corp", "total_cents": 1299}
    assert result.confidences == {"vendor": 0.97, "total_cents": 0.62}
    assert result.cost_cents > 0


@pytest.mark.asyncio
async def test_confidence_defaults_to_zero_when_missing_field() -> None:
    """A field present in ``data`` but missing from ``confidence`` lands
    at 0.0 so the orchestrator treats it as flagged for review."""
    fake_envelope = _make_envelope(
        data=_Receipt(vendor="Acme", total_cents=1299),
        confidence={"vendor": 0.97},
    )
    fake_response = SimpleNamespace(output_parsed=fake_envelope)

    with patch(
        "awaithumans.server.services.demo.extractor._build_client"
    ) as mock_client_builder:
        client = AsyncMock()
        client.responses.parse = AsyncMock(return_value=fake_response)
        mock_client_builder.return_value = client

        result = await run_demo_extraction(
            page_png=b"\x89PNG fake",
            response_model=_Receipt,
        )

    assert result.confidences == {"vendor": 0.97, "total_cents": 0.0}


@pytest.mark.asyncio
async def test_confidence_clamped_to_unit_interval() -> None:
    """Out-of-range confidences clamp into [0.0, 1.0]. Some models return
    1.2 ("very confident") or negative numbers when they hallucinate the
    scoring rubric; we squash those rather than propagate."""
    fake_envelope = _make_envelope(
        data=_Receipt(vendor="Acme", total_cents=1299),
        confidence={"vendor": 1.5, "total_cents": -0.4},
    )
    fake_response = SimpleNamespace(output_parsed=fake_envelope)

    with patch(
        "awaithumans.server.services.demo.extractor._build_client"
    ) as mock_client_builder:
        client = AsyncMock()
        client.responses.parse = AsyncMock(return_value=fake_response)
        mock_client_builder.return_value = client

        result = await run_demo_extraction(
            page_png=b"\x89PNG fake",
            response_model=_Receipt,
        )

    assert result.confidences == {"vendor": 1.0, "total_cents": 0.0}


@pytest.mark.asyncio
async def test_raises_when_output_parsed_is_none() -> None:
    """The SDK populates ``output_parsed`` with the validated model on
    success; ``None`` means the model failed schema validation or the
    response wasn't structured. Surface a sanitized DemoProviderError."""
    fake_response = SimpleNamespace(output_parsed=None)

    with patch(
        "awaithumans.server.services.demo.extractor._build_client"
    ) as mock_client_builder:
        client = AsyncMock()
        client.responses.parse = AsyncMock(return_value=fake_response)
        mock_client_builder.return_value = client

        with pytest.raises(DemoProviderError):
            await run_demo_extraction(
                page_png=b"\x89PNG fake",
                response_model=_Receipt,
            )


@pytest.mark.asyncio
async def test_raises_on_sdk_error() -> None:
    """Any SDK-side exception (network, auth, rate limit) gets wrapped
    in DemoProviderError so the wizard shows a sanitized message."""
    with patch(
        "awaithumans.server.services.demo.extractor._build_client"
    ) as mock_client_builder:
        client = AsyncMock()
        client.responses.parse = AsyncMock(side_effect=RuntimeError("API down"))
        mock_client_builder.return_value = client

        with pytest.raises(DemoProviderError):
            await run_demo_extraction(
                page_png=b"\x89PNG fake",
                response_model=_Receipt,
            )
