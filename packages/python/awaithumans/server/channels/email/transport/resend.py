"""Resend transport.

Uses Resend's HTTPS API (https://resend.com/docs/api-reference). We call
it with httpx rather than the `resend` SDK because httpx is already a
base dependency and the API surface we need is three fields. If we add
tags/scheduling/batch, swap to the SDK.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from awaithumans.server.channels.email.transport.base import (
    EmailMessage,
    EmailSendResult,
    EmailTransportError,
)

logger = logging.getLogger("awaithumans.server.channels.email.transport.resend")

_RESEND_API_URL = "https://api.resend.com/emails"


class ResendTransport:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise EmailTransportError("Resend transport requires a non-empty api_key.")
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "resend"

    async def send(self, message: EmailMessage) -> EmailSendResult:
        body: dict[str, Any] = {
            "from": message.formatted_from(),
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
        }
        if message.reply_to:
            body["reply_to"] = message.reply_to
        if message.tags:
            body["tags"] = [{"name": k, "value": v} for k, v in message.tags.items()]

        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                _RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )

        if resp.status_code >= 400:
            logger.error(
                "Resend send failed: status=%s body=%s",
                resp.status_code,
                resp.text[:500],
            )
            raise EmailTransportError(f"Resend returned HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json() if resp.content else {}
        return EmailSendResult(
            message_id=data.get("id"),
            transport=self.name,
        )
