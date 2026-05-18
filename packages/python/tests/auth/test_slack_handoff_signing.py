"""Slack-handoff URL signing — the proof-of-identity for Slack-only users.

A Slack-only user (no email/password in the directory) has no way to
clear the dashboard's login wall. The notifier signs a URL with
(user_id, task_id, expiry); the recipient clicks it; the endpoint
verifies the signature and exchanges it for a session cookie. These
tests pin the signing layer end-to-end-ish without spinning up the
HTTP server — verification is what the real attack would target.

Wired through the same `_payload_key` fixture as session-token tests
so we exercise the actual HKDF path.
"""

from __future__ import annotations

import time

import pytest

from awaithumans.server.core.slack_handoff import (
    InvalidHandoffError,
    sign_handoff,
    verify_handoff,
)

USER = "user_" + "a" * 28
TASK = "task_" + "b" * 27


def _far_future() -> int:
    """An expiry comfortably beyond test wall time."""
    return int(time.time()) + 3600


# ─── Happy path ──────────────────────────────────────────────────────


def test_roundtrip_accepts_valid_signature() -> None:
    exp = _far_future()
    sig = sign_handoff(user_id=USER, task_id=TASK, exp_unix=exp)
    # No raise == accepted.
    verify_handoff(user_id=USER, task_id=TASK, exp_unix=exp, signature=sig)


def test_signature_is_deterministic() -> None:
    """Same inputs → same signature. Lets us reason about caching and
    URL-stability across retries (Slack click handlers may re-emit
    the same URL on a re-render)."""
    exp = _far_future()
    a = sign_handoff(user_id=USER, task_id=TASK, exp_unix=exp)
    b = sign_handoff(user_id=USER, task_id=TASK, exp_unix=exp)
    assert a == b


def test_signature_changes_when_any_field_changes() -> None:
    """Bind tightly: changing user, task, or expiry must invalidate the
    signature so a leaked URL for one task can't be re-pointed at
    another."""
    exp = _far_future()
    base = sign_handoff(user_id=USER, task_id=TASK, exp_unix=exp)
    assert sign_handoff(user_id="other", task_id=TASK, exp_unix=exp) != base
    assert sign_handoff(user_id=USER, task_id="other", exp_unix=exp) != base
    assert sign_handoff(user_id=USER, task_id=TASK, exp_unix=exp + 1) != base


# ─── Tamper / replay ─────────────────────────────────────────────────


def test_wrong_user_rejected() -> None:
    """Signature is bound to user_id — verify with a different user
    must fail. Without this, a leaked URL would be a generic login."""
    exp = _far_future()
    sig = sign_handoff(user_id=USER, task_id=TASK, exp_unix=exp)
    with pytest.raises(InvalidHandoffError, match="signature mismatch"):
        verify_handoff(user_id="other_user", task_id=TASK, exp_unix=exp, signature=sig)


def test_wrong_task_rejected() -> None:
    """A URL signed for task A must not authenticate for task B —
    binding to task_id is what scopes the URL."""
    exp = _far_future()
    sig = sign_handoff(user_id=USER, task_id=TASK, exp_unix=exp)
    with pytest.raises(InvalidHandoffError, match="signature mismatch"):
        verify_handoff(user_id=USER, task_id="other_task", exp_unix=exp, signature=sig)


def test_expiry_in_the_past_rejected() -> None:
    """A URL whose `e=` is older than wall-clock now must fail
    verification — the signature is still cryptographically valid,
    but the clock check catches replay-after-expiry."""
    expired = int(time.time()) - 1
    sig = sign_handoff(user_id=USER, task_id=TASK, exp_unix=expired)
    with pytest.raises(InvalidHandoffError, match="expired"):
        verify_handoff(user_id=USER, task_id=TASK, exp_unix=expired, signature=sig)


def test_garbage_signature_rejected() -> None:
    """A non-base64 / wrong-length blob fails fast — no key
    derivation needed."""
    with pytest.raises(InvalidHandoffError):
        verify_handoff(user_id=USER, task_id=TASK, exp_unix=_far_future(), signature="!!")


def test_empty_signature_rejected() -> None:
    with pytest.raises(InvalidHandoffError, match="missing signature"):
        verify_handoff(user_id=USER, task_id=TASK, exp_unix=_far_future(), signature="")


def test_signature_wrong_length_rejected() -> None:
    """A 5-byte string round-trips as base64 but isn't a 32-byte
    HMAC — reject before doing the constant-time compare."""
    import base64

    short = base64.urlsafe_b64encode(b"short").decode().rstrip("=")
    with pytest.raises(InvalidHandoffError, match="wrong length"):
        verify_handoff(user_id=USER, task_id=TASK, exp_unix=_far_future(), signature=short)
