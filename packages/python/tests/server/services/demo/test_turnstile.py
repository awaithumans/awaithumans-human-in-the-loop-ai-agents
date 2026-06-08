from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from awaithumans.server.services.demo.exceptions import TurnstileVerificationError
from awaithumans.server.services.demo.turnstile import verify_turnstile_token


@pytest.mark.asyncio
async def test_passes_on_success(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://challenges.cloudflare.com/turnstile/v0/siteverify",
        json={"success": True},
    )
    await verify_turnstile_token(token="ok-token", remote_ip="1.2.3.4", secret="sek")


@pytest.mark.asyncio
async def test_raises_on_failure(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://challenges.cloudflare.com/turnstile/v0/siteverify",
        json={"success": False, "error-codes": ["bad-token"]},
    )
    with pytest.raises(TurnstileVerificationError):
        await verify_turnstile_token(token="bad-token", remote_ip="1.2.3.4", secret="sek")


@pytest.mark.asyncio
async def test_raises_on_http_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://challenges.cloudflare.com/turnstile/v0/siteverify",
        status_code=500,
    )
    with pytest.raises(TurnstileVerificationError):
        await verify_turnstile_token(token="t", remote_ip="1.2.3.4", secret="sek")


@pytest.mark.asyncio
async def test_raises_on_empty_token() -> None:
    # No HTTP call expected; should reject before going to network.
    with pytest.raises(TurnstileVerificationError):
        await verify_turnstile_token(token="", remote_ip="1.2.3.4", secret="sek")


@pytest.mark.asyncio
async def test_raises_on_empty_secret() -> None:
    with pytest.raises(TurnstileVerificationError):
        await verify_turnstile_token(token="t", remote_ip="1.2.3.4", secret="")
