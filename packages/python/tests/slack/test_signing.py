"""Slack request signature verification."""

from __future__ import annotations

import hashlib
import hmac
import time

from awaithumans.server.channels.slack.signing import verify_signature
from awaithumans.utils.constants import SLACK_SIGNATURE_MAX_AGE_SECONDS

SECRET = "test-signing-secret"


def _sign(body: bytes, timestamp: str, secret: str = SECRET) -> str:
    basestring = b"v0:" + timestamp.encode() + b":" + body
    digest = hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def test_valid_signature_passes() -> None:
    body = b'{"type":"view_submission"}'
    ts = str(int(time.time()))
    sig = _sign(body, ts)
    assert verify_signature(body=body, timestamp=ts, signature=sig, signing_secret=SECRET) is True


def test_tampered_body_fails() -> None:
    body = b'{"type":"view_submission"}'
    ts = str(int(time.time()))
    sig = _sign(body, ts)
    tampered = b'{"type":"view_submission","malicious":true}'
    assert (
        verify_signature(body=tampered, timestamp=ts, signature=sig, signing_secret=SECRET) is False
    )


def test_wrong_secret_fails() -> None:
    body = b"{}"
    ts = str(int(time.time()))
    sig = _sign(body, ts, secret="attacker-secret")
    assert verify_signature(body=body, timestamp=ts, signature=sig, signing_secret=SECRET) is False


def test_stale_timestamp_rejected() -> None:
    body = b"{}"
    # Older than max age → rejected even with a valid signature.
    ts = str(int(time.time()) - SLACK_SIGNATURE_MAX_AGE_SECONDS - 1)
    sig = _sign(body, ts)
    assert verify_signature(body=body, timestamp=ts, signature=sig, signing_secret=SECRET) is False


def test_future_timestamp_rejected() -> None:
    body = b"{}"
    ts = str(int(time.time()) + SLACK_SIGNATURE_MAX_AGE_SECONDS + 1)
    sig = _sign(body, ts)
    assert verify_signature(body=body, timestamp=ts, signature=sig, signing_secret=SECRET) is False


def test_missing_fields_fail() -> None:
    body = b"{}"
    ts = str(int(time.time()))
    sig = _sign(body, ts)
    assert (
        verify_signature(body=body, timestamp=None, signature=sig, signing_secret=SECRET) is False
    )
    assert verify_signature(body=body, timestamp=ts, signature=None, signing_secret=SECRET) is False
    assert verify_signature(body=body, timestamp=ts, signature=sig, signing_secret="") is False


def test_non_integer_timestamp_fails() -> None:
    body = b"{}"
    assert (
        verify_signature(
            body=body, timestamp="not-a-number", signature="v0=x", signing_secret=SECRET
        )
        is False
    )
