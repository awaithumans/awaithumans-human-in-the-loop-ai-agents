"""Magic-link action tokens — HMAC verification + tamper + expiry."""

from __future__ import annotations

import base64
import json
import time
from unittest.mock import patch

import pytest

from awaithumans.server.channels.email.magic_links import (
    InvalidActionTokenError,
    sign_action_token,
    verify_action_token,
)
from awaithumans.utils.constants import MAGIC_LINK_MAX_AGE_SECONDS


def test_roundtrip_boolean_value() -> None:
    token = sign_action_token(task_id="t1", field_name="approve", value=True)
    claim = verify_action_token(token)
    assert claim.task_id == "t1"
    assert claim.field_name == "approve"
    assert claim.value is True
    # Single-use enforcement carries jti through the wire.
    assert claim.jti and len(claim.jti) >= 16


def test_each_signed_token_gets_a_fresh_jti() -> None:
    """Two calls to `sign_action_token` with identical args produce
    different tokens because the jti is randomised. Without that,
    two emails sent for the same option would share a jti and the
    second couldn't be consumed even if the first hadn't been."""
    a = sign_action_token(task_id="t1", field_name="approve", value=True)
    b = sign_action_token(task_id="t1", field_name="approve", value=True)
    assert a != b
    claim_a = verify_action_token(a)
    claim_b = verify_action_token(b)
    assert claim_a.jti != claim_b.jti


def test_explicit_jti_overrides_random() -> None:
    """The `jti=` kwarg lets tests pin a known value for assertions."""
    token = sign_action_token(task_id="t1", field_name="approve", value=True, jti="fixed-jti-x")
    claim = verify_action_token(token)
    assert claim.jti == "fixed-jti-x"


def test_roundtrip_string_value() -> None:
    token = sign_action_token(task_id="t1", field_name="tier", value="gold")
    claim = verify_action_token(token)
    assert claim.value == "gold"


def test_tampered_signature_rejected() -> None:
    token = sign_action_token(task_id="t1", field_name="approve", value=True)
    # Flip a char in the middle.
    pos = len(token) // 2
    tampered = token[:pos] + ("A" if token[pos] != "A" else "B") + token[pos + 1 :]
    with pytest.raises(InvalidActionTokenError):
        verify_action_token(tampered)


def test_tampered_body_rejected() -> None:
    """Even a valid-looking JSON body with swapped value fails HMAC."""
    token = sign_action_token(task_id="t1", field_name="approve", value=True)
    padded = token + "=" * (-len(token) % 4)
    blob = bytearray(base64.urlsafe_b64decode(padded))
    # Find the body portion (after 32-byte HMAC) and rewrite "v":true → false.
    body = blob[32:].decode()
    body_mod = body.replace(',"v":true', ',"v":false')
    assert body_mod != body  # sanity check — replacement happened
    tampered_blob = blob[:32] + body_mod.encode()
    tampered_token = base64.urlsafe_b64encode(bytes(tampered_blob)).decode().rstrip("=")
    with pytest.raises(InvalidActionTokenError, match="signature"):
        verify_action_token(tampered_token)


def test_expired_rejected() -> None:
    token = sign_action_token(task_id="t1", field_name="approve", value=True)
    # Fast-forward past TTL.
    future = time.time() + MAGIC_LINK_MAX_AGE_SECONDS + 10
    with (
        patch(
            "awaithumans.server.channels.email.magic_links.time.time",
            return_value=future,
        ),
        pytest.raises(InvalidActionTokenError, match="expired"),
    ):
        verify_action_token(token)


def test_short_ttl_honored() -> None:
    token = sign_action_token(task_id="t1", field_name="approve", value=True, ttl_seconds=1)
    # Still valid immediately
    verify_action_token(token)
    # Expired after 2 seconds (via patched clock)
    future = time.time() + 5
    with (
        patch(
            "awaithumans.server.channels.email.magic_links.time.time",
            return_value=future,
        ),
        pytest.raises(InvalidActionTokenError, match="expired"),
    ):
        verify_action_token(token)


def test_recipient_round_trips_through_token() -> None:
    """The renderer signs the recipient email into every action token.
    The action route reads `claim.recipient` and stamps it as
    `completed_by_email` so the audit log isn't a black hole for
    email completions. Pin the round-trip."""
    token = sign_action_token(
        task_id="t1",
        field_name="approve",
        value=True,
        recipient="alice@acme.com",
    )
    claim = verify_action_token(token)
    assert claim.recipient == "alice@acme.com"


def test_recipient_omitted_yields_none() -> None:
    """Pre-feature tokens (no `r` field in the signed body) verify
    cleanly — the audit log just shows "—" the way it did before
    this change. Tested by signing without `recipient=`."""
    token = sign_action_token(task_id="t1", field_name="approve", value=True)
    claim = verify_action_token(token)
    assert claim.recipient is None


def test_malformed_inputs_rejected() -> None:
    with pytest.raises(InvalidActionTokenError):
        verify_action_token("")
    with pytest.raises(InvalidActionTokenError):
        verify_action_token("!!!not-base64!!!")
    with pytest.raises(InvalidActionTokenError, match="too short"):
        verify_action_token(base64.urlsafe_b64encode(b"short").decode().rstrip("="))


def test_missing_payload_fields_rejected() -> None:
    """A hand-crafted token with the right HMAC but missing claim fields must fail."""
    # We can't forge an HMAC without the key, so craft a token using
    # sign_action_token first, then replace the body with a malformed one
    # — that will fail signature, not body-parse, but it proves the defense
    # runs in order (signature first). Swap the JSON for an object missing 'e'.
    import hashlib
    import hmac as _hmac

    from awaithumans.server.channels.email.magic_links import _hmac_key

    bad_body = json.dumps({"t": "x", "f": "y", "v": True}).encode()
    mac = _hmac.new(_hmac_key(), bad_body, hashlib.sha256).digest()
    blob = mac + bad_body
    token = base64.urlsafe_b64encode(blob).decode().rstrip("=")
    with pytest.raises(InvalidActionTokenError, match="missing fields"):
        verify_action_token(token)
