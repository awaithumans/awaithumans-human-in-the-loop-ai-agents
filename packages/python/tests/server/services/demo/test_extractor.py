from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from awaithumans.server.services.demo.exceptions import DemoProviderError
from awaithumans.server.services.demo.extractor import (
    ExtractionResult,
    run_demo_extraction,
)


class _Receipt(BaseModel):
    vendor: str
    total_cents: int


def _mock_chat_completion(text: str) -> Any:
    """Build an object shaped like `openai.types.chat.ChatCompletion`.

    The extractor only reads `response.choices[0].message.content`, so a
    nested SimpleNamespace is enough to stand in for the real SDK
    response without pulling in the full pydantic models.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text),
            )
        ],
    )


@pytest.mark.asyncio
async def test_happy_path() -> None:
    response_text = (
        '{"data": {"vendor": "Acme Corp", "total_cents": 1299}, '
        '"confidence": {"vendor": 0.97, "total_cents": 0.62}}'
    )
    with patch("awaithumans.server.services.demo.extractor._build_client") as mock_client_builder:
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(
            return_value=_mock_chat_completion(response_text)
        )
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
async def test_raises_on_invalid_json() -> None:
    with patch("awaithumans.server.services.demo.extractor._build_client") as mock_client_builder:
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=_mock_chat_completion("not json"))
        mock_client_builder.return_value = client

        with pytest.raises(DemoProviderError):
            await run_demo_extraction(
                page_png=b"\x89PNG fake",
                response_model=_Receipt,
            )


@pytest.mark.asyncio
async def test_raises_on_missing_data_key() -> None:
    with patch("awaithumans.server.services.demo.extractor._build_client") as mock_client_builder:
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(
            return_value=_mock_chat_completion('{"confidence": {"vendor": 0.9}}')
        )
        mock_client_builder.return_value = client

        with pytest.raises(DemoProviderError):
            await run_demo_extraction(
                page_png=b"\x89PNG fake",
                response_model=_Receipt,
            )


@pytest.mark.asyncio
async def test_raises_on_schema_mismatch() -> None:
    response_text = (
        '{"data": {"vendor": "Acme"}, '
        '"confidence": {"vendor": 0.97}}'
    )  # missing total_cents required field
    with patch("awaithumans.server.services.demo.extractor._build_client") as mock_client_builder:
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(
            return_value=_mock_chat_completion(response_text)
        )
        mock_client_builder.return_value = client

        with pytest.raises(DemoProviderError):
            await run_demo_extraction(
                page_png=b"\x89PNG fake",
                response_model=_Receipt,
            )


@pytest.mark.asyncio
async def test_raises_on_sdk_error() -> None:
    with patch("awaithumans.server.services.demo.extractor._build_client") as mock_client_builder:
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(side_effect=RuntimeError("API down"))
        mock_client_builder.return_value = client

        with pytest.raises(DemoProviderError):
            await run_demo_extraction(
                page_png=b"\x89PNG fake",
                response_model=_Receipt,
            )


@pytest.mark.asyncio
async def test_confidence_defaults_to_zero_when_missing_field() -> None:
    # Field returned in `data` but missing from `confidence` should
    # default to 0.0 (treat as flagged).
    response_text = (
        '{"data": {"vendor": "Acme", "total_cents": 1299}, "confidence": {"vendor": 0.97}}'
    )
    with patch("awaithumans.server.services.demo.extractor._build_client") as mock_client_builder:
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(
            return_value=_mock_chat_completion(response_text)
        )
        mock_client_builder.return_value = client

        result = await run_demo_extraction(
            page_png=b"\x89PNG fake",
            response_model=_Receipt,
        )
    assert result.confidences == {"vendor": 0.97, "total_cents": 0.0}
