"""Signed-URL handoff: Slack DM → dashboard session.

A Slack-only user (no email/password in the directory) has no way to
clear the dashboard's login wall — clicking "Open in Dashboard" on the
Slack notification just bounces them to /login. This module mints a
URL that doubles as proof-of-identity: the Slack notifier signs
(user_id, task_id, expiry) at post time, the recipient clicks it, the
endpoint verifies the signature and exchanges it for a real session
cookie before redirecting to the task page.

URL shape: `/api/auth/slack-handoff?u=<user_id>&t=<task_id>&e=<exp>&s=<sig>`

  - `u` is the directory user_id we issued the link to. The session
    is minted for THIS user; the URL is not a generic login.
  - `t` is the task_id the recipient was sent. We bind to it so a
    leaked URL can't be reused to read other tasks.
  - `e` is the Unix timestamp the URL stops being valid. Set to
    `task.timeout_at` at sign time so the link works for the whole
    task lifetime (a 7-day approval still has a working link on day 6).
  - `s` is HMAC-SHA256 over `u|t|e` with a key HKDF-derived from
    `PAYLOAD_KEY` under a slack-handoff salt — same key never signs
    two primitives.

We deliberately don't enforce single-use here. The URL is delivered
via Slack DM (recipient-only), the expiry caps the damage, and the
normal session cookie that gets minted has its own short TTL. Adding
a `consumed_token` row per click would block the legitimate "I closed
the tab, click the link again" flow.

When the task is terminal at click time we still mint the session and
redirect — the dashboard renders the task as read-only, the user sees
the outcome, and there's no point making them stare at a 410.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from awaithumans.server.core.encryption import get_key
from awaithumans.utils.constants import (
    HMAC_SHA256_DIGEST_BYTES,
    SLACK_HANDOFF_HKDF_INFO,
    SLACK_HANDOFF_HKDF_SALT,
)

logger = logging.getLogger("awaithumans.server.core.slack_handoff")


class InvalidHandoffError(Exception):
    """The signed URL failed format / HMAC / expiry validation."""


def _hmac_key() -> bytes:
    return HKDF(
        algorithm=SHA256(),
        length=HMAC_SHA256_DIGEST_BYTES,
        salt=SLACK_HANDOFF_HKDF_SALT,
        info=SLACK_HANDOFF_HKDF_INFO,
    ).derive(get_key())


def _canonical_message(user_id: str, task_id: str, exp: int) -> bytes:
    """Stable byte-encoding for the signed payload.

    Pipe-separator avoids JSON ambiguity (key ordering, whitespace) for
    a 3-field tuple. None of the values can contain a literal `|` —
    user_ids and task_ids are uuids/uuid-like, exp is an int.
    """
    return f"{user_id}|{task_id}|{exp}".encode()


def sign_handoff(*, user_id: str, task_id: str, exp_unix: int) -> str:
    """Produce the URL-safe signature for a handoff URL.

    Caller is responsible for assembling the full URL with the rest of
    the params. Caller also picks `exp_unix` — typically the task's
    `timeout_at` so the link expires when the task does."""
    mac = hmac.new(
        _hmac_key(),
        _canonical_message(user_id, task_id, exp_unix),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def verify_handoff(*, user_id: str, task_id: str, exp_unix: int, signature: str) -> None:
    """Validate the URL's signature + expiry. Raises `InvalidHandoffError`
    on any failure; returns None on success."""
    if not signature:
        raise InvalidHandoffError("missing signature")

    padded = signature + "=" * (-len(signature) % 4)
    try:
        mac = base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise InvalidHandoffError(f"signature not base64: {exc}") from exc

    if len(mac) != HMAC_SHA256_DIGEST_BYTES:
        raise InvalidHandoffError("signature wrong length")

    expected = hmac.new(
        _hmac_key(),
        _canonical_message(user_id, task_id, exp_unix),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected, mac):
        raise InvalidHandoffError("signature mismatch")

    if time.time() > exp_unix:
        raise InvalidHandoffError("expired")
