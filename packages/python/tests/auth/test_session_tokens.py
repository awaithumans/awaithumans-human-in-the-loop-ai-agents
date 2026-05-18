"""Session-cookie HMAC — signing, verify, tamper, expiry.

Tests the low-level token machinery independent of DB state. The
`_payload_key` fixture from conftest.py seeds PAYLOAD_KEY for each
test so HMAC derivation works.
"""

from __future__ import annotations

import base64
import time

import pytest

from awaithumans.server.core.auth import (
    InvalidSessionError,
    SessionClaims,
    sign_session,
    verify_session,
)
from awaithumans.utils.constants import HMAC_SHA256_DIGEST_BYTES

# Stable user ID to sign tokens against — matches the format new_id()
# produces (32 hex chars). Not inserted in DB; these tests stay at the
# cookie layer.
USER_ID = "deadbeef" * 4


def test_roundtrip() -> None:
    token = sign_session(user_id=USER_ID, is_operator=True)
    claims = verify_session(token)
    assert isinstance(claims, SessionClaims)
    assert claims.user_id == USER_ID
    assert claims.is_operator is True


def test_operator_flag_round_trips() -> None:
    op_token = sign_session(user_id=USER_ID, is_operator=True)
    non_op_token = sign_session(user_id=USER_ID, is_operator=False)
    assert verify_session(op_token).is_operator is True
    assert verify_session(non_op_token).is_operator is False


def test_sessions_vary_by_time() -> None:
    a = sign_session(user_id=USER_ID, is_operator=False)
    time.sleep(1.1)  # tokens embed int(time.time()) so 1s resolution
    b = sign_session(user_id=USER_ID, is_operator=False)
    assert a != b


def test_tampered_body_rejected() -> None:
    token = sign_session(user_id=USER_ID, is_operator=False)
    raw = bytearray(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    raw[-1] ^= 0x01
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")
    with pytest.raises(InvalidSessionError):
        verify_session(tampered)


def test_tampered_mac_rejected() -> None:
    token = sign_session(user_id=USER_ID, is_operator=False)
    raw = bytearray(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    raw[0] ^= 0x01
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")
    with pytest.raises(InvalidSessionError):
        verify_session(tampered)


def test_expired_rejected() -> None:
    token = sign_session(user_id=USER_ID, is_operator=False, ttl_seconds=-1)
    with pytest.raises(InvalidSessionError, match="expired"):
        verify_session(token)


def test_empty_rejected() -> None:
    with pytest.raises(InvalidSessionError):
        verify_session("")


def test_bad_base64_rejected() -> None:
    with pytest.raises(InvalidSessionError):
        verify_session("!!!not base64!!!")


def test_too_short_rejected() -> None:
    short = base64.urlsafe_b64encode(b"x" * (HMAC_SHA256_DIGEST_BYTES - 1)).decode()
    with pytest.raises(InvalidSessionError):
        verify_session(short)


def test_malformed_body_rejected() -> None:
    """Valid HMAC but body isn't the expected {u, o, e} shape."""
    import hashlib
    import hmac as hmac_mod

    from awaithumans.server.core.auth import _hmac_key

    body = b'{"not": "our shape"}'
    mac = hmac_mod.new(_hmac_key(), body, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(mac + body).decode().rstrip("=")
    with pytest.raises(InvalidSessionError, match="malformed"):
        verify_session(token)
