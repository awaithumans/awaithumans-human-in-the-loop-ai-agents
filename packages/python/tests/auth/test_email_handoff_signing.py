"""Email-handoff URL signing — proof-of-identity for email recipients.

Mirror of `test_slack_handoff_signing.py`. The "Review in dashboard"
link in a notification email is HMAC-signed by the renderer; the
endpoint verifies and exchanges it for a session cookie. These
tests pin the signing layer end-to-end without spinning up the
HTTP server.
"""

from __future__ import annotations

import time

import pytest

from awaithumans.server.core.email_handoff import (
    InvalidHandoffError,
    sign_handoff,
    verify_handoff,
)

RECIPIENT = "alice@acme.com"
TASK = "task_" + "b" * 27


def _far_future() -> int:
    return int(time.time()) + 3600


# ─── Happy path ──────────────────────────────────────────────────────


def test_roundtrip_accepts_valid_signature() -> None:
    exp = _far_future()
    sig = sign_handoff(recipient=RECIPIENT, task_id=TASK, exp_unix=exp)
    verify_handoff(recipient=RECIPIENT, task_id=TASK, exp_unix=exp, signature=sig)


def test_signature_is_deterministic() -> None:
    exp = _far_future()
    a = sign_handoff(recipient=RECIPIENT, task_id=TASK, exp_unix=exp)
    b = sign_handoff(recipient=RECIPIENT, task_id=TASK, exp_unix=exp)
    assert a == b


def test_signature_is_case_insensitive_on_recipient() -> None:
    """Real-world senders and recipients sometimes case-flip — the
    same address has a stable signature regardless of casing so a
    re-rendered email doesn't break inbox links."""
    exp = _far_future()
    a = sign_handoff(recipient="Alice@Acme.com", task_id=TASK, exp_unix=exp)
    b = sign_handoff(recipient="alice@acme.com", task_id=TASK, exp_unix=exp)
    assert a == b
    # And verification accepts either form.
    verify_handoff(recipient="ALICE@ACME.COM", task_id=TASK, exp_unix=exp, signature=a)


def test_signature_changes_when_any_field_changes() -> None:
    exp = _far_future()
    base = sign_handoff(recipient=RECIPIENT, task_id=TASK, exp_unix=exp)
    assert sign_handoff(recipient="other@acme.com", task_id=TASK, exp_unix=exp) != base
    assert sign_handoff(recipient=RECIPIENT, task_id="other", exp_unix=exp) != base
    assert sign_handoff(recipient=RECIPIENT, task_id=TASK, exp_unix=exp + 1) != base


# ─── Tamper / replay ─────────────────────────────────────────────────


def test_wrong_recipient_rejected() -> None:
    exp = _far_future()
    sig = sign_handoff(recipient=RECIPIENT, task_id=TASK, exp_unix=exp)
    with pytest.raises(InvalidHandoffError, match="signature mismatch"):
        verify_handoff(
            recipient="someone-else@acme.com",
            task_id=TASK,
            exp_unix=exp,
            signature=sig,
        )


def test_wrong_task_rejected() -> None:
    exp = _far_future()
    sig = sign_handoff(recipient=RECIPIENT, task_id=TASK, exp_unix=exp)
    with pytest.raises(InvalidHandoffError, match="signature mismatch"):
        verify_handoff(
            recipient=RECIPIENT,
            task_id="other_task",
            exp_unix=exp,
            signature=sig,
        )


def test_expiry_in_the_past_rejected() -> None:
    expired = int(time.time()) - 1
    sig = sign_handoff(recipient=RECIPIENT, task_id=TASK, exp_unix=expired)
    with pytest.raises(InvalidHandoffError, match="expired"):
        verify_handoff(
            recipient=RECIPIENT,
            task_id=TASK,
            exp_unix=expired,
            signature=sig,
        )


def test_pipe_in_recipient_rejected() -> None:
    """Canonical message uses pipe separators; any `|` in the recipient
    would break parsing. Real addresses never contain `|`; the few
    RFC corner cases that would are not worth the complexity."""
    with pytest.raises(InvalidHandoffError, match="contains '\\|'"):
        sign_handoff(
            recipient="alice|odd@acme.com",
            task_id=TASK,
            exp_unix=_far_future(),
        )


def test_garbage_signature_rejected() -> None:
    with pytest.raises(InvalidHandoffError):
        verify_handoff(
            recipient=RECIPIENT,
            task_id=TASK,
            exp_unix=_far_future(),
            signature="!!",
        )


def test_empty_signature_rejected() -> None:
    with pytest.raises(InvalidHandoffError, match="missing signature"):
        verify_handoff(
            recipient=RECIPIENT,
            task_id=TASK,
            exp_unix=_far_future(),
            signature="",
        )
