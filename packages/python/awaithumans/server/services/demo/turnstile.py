"""Cloudflare Turnstile token verification.

Server-side check using the secret. Treat any non-success response
(network error, non-2xx, success=false, empty token, empty secret) as
a verification failure and let the centralized handler return 403.
"""

from __future__ import annotations

import logging

import httpx

from awaithumans.server.services.demo.exceptions import TurnstileVerificationError

logger = logging.getLogger("awaithumans.server.services.demo.turnstile")

_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_TIMEOUT_SECONDS = 5.0


async def verify_turnstile_token(
    *,
    token: str,
    remote_ip: str | None,
    secret: str,
) -> None:
    if not token:
        raise TurnstileVerificationError()
    if not secret:
        logger.error("Turnstile secret not configured; rejecting submit.")
        raise TurnstileVerificationError()

    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(_VERIFY_URL, data=payload)
    except httpx.RequestError as exc:
        logger.warning("Turnstile verify HTTP error: %s", exc)
        raise TurnstileVerificationError() from exc

    if response.status_code != 200:
        logger.warning("Turnstile verify non-200: %s", response.status_code)
        raise TurnstileVerificationError()

    body = response.json()
    if not body.get("success"):
        logger.info("Turnstile rejected token: %s", body.get("error-codes"))
        raise TurnstileVerificationError()
