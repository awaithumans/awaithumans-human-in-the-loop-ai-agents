"""Signed-URL handoff: email recipient → dashboard session.

Mirror of `slack_handoff.py` for the email channel. Lets the
"Review in dashboard" link in a notification email drop the
recipient straight into the task page, authenticated, without
requiring them to have a dashboard password.

URL shape: `/api/auth/email-handoff?to=<email>&t=<task_id>&e=<exp>&s=<sig>`

  - `to` is the recipient address the link was issued to. The
    session is minted for the directory user with that email; if
    no row exists, the endpoint auto-provisions a passwordless
    reviewer (the agent's `notify=` already represents implicit
    consent to provision — same trust boundary as task creation).
  - `t` is the task_id the recipient was sent. We bind to it so a
    leaked URL can't be reused to read other tasks.
  - `e` is the Unix timestamp the URL stops being valid. Set to
    `task.timeout_at` at sign time so the link works for the whole
    task lifetime, just like the Slack handoff.
  - `s` is HMAC-SHA256 over `to|t|e` with a key HKDF-derived from
    PAYLOAD_KEY under an email-handoff salt — same root key never
    signs two primitives.

We deliberately don't enforce single-use here. The URL is delivered
via email (recipient-only), the expiry caps the damage, and the
session cookie that gets minted has its own short TTL. Adding a
`consumed_token` row per click would block the legitimate "I closed
the tab, click the link again" flow.
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
    EMAIL_HANDOFF_HKDF_INFO,
    EMAIL_HANDOFF_HKDF_SALT,
    HMAC_SHA256_DIGEST_BYTES,
)

logger = logging.getLogger("awaithumans.server.core.email_handoff")


class InvalidHandoffError(Exception):
    """The signed URL failed format / HMAC / expiry validation."""


def _hmac_key() -> bytes:
    return HKDF(
        algorithm=SHA256(),
        length=HMAC_SHA256_DIGEST_BYTES,
        salt=EMAIL_HANDOFF_HKDF_SALT,
        info=EMAIL_HANDOFF_HKDF_INFO,
    ).derive(get_key())


def _canonical_message(recipient: str, task_id: str, exp: int) -> bytes:
    """Stable byte-encoding for the signed payload.

    Pipe-separator avoids JSON ambiguity for a 3-field tuple. Email
    addresses can theoretically contain `|` (RFC 5321 quoted-string),
    so we lowercase and reject the rare-but-possible bar character
    at sign / verify boundaries — see `_normalize_recipient`.
    """
    return f"{recipient}|{task_id}|{exp}".encode()


def _normalize_recipient(recipient: str) -> str:
    """Lowercase + reject pipe-character to keep canonical-message
    parsing unambiguous. Real-world senders never see `|` in a real
    address; the few RFC corner cases that would are not worth
    supporting in dev mode."""
    if "|" in recipient:
        raise InvalidHandoffError("recipient contains '|' — refused")
    return recipient.lower()


def sign_handoff(*, recipient: str, task_id: str, exp_unix: int) -> str:
    """Produce the URL-safe signature for an email handoff URL.

    Caller is responsible for assembling the full URL with the rest
    of the params. `exp_unix` is typically `task.timeout_at` so the
    link expires when the task does."""
    normalized = _normalize_recipient(recipient)
    mac = hmac.new(
        _hmac_key(),
        _canonical_message(normalized, task_id, exp_unix),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def verify_handoff(*, recipient: str, task_id: str, exp_unix: int, signature: str) -> None:
    """Validate the URL's signature + expiry. Raises on any failure."""
    if not signature:
        raise InvalidHandoffError("missing signature")

    normalized = _normalize_recipient(recipient)

    padded = signature + "=" * (-len(signature) % 4)
    try:
        mac = base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise InvalidHandoffError(f"signature not base64: {exc}") from exc

    if len(mac) != HMAC_SHA256_DIGEST_BYTES:
        raise InvalidHandoffError("signature wrong length")

    expected = hmac.new(
        _hmac_key(),
        _canonical_message(normalized, task_id, exp_unix),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected, mac):
        raise InvalidHandoffError("signature mismatch")

    if time.time() > exp_unix:
        raise InvalidHandoffError("expired")
