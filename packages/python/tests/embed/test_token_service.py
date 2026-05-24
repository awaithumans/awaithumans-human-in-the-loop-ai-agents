"""Tests for embed_token_service sign/verify primitives.

Covers:
  1. Round-trip: sign then verify returns correct EmbedClaims.
  2. Tampered signature rejected.
  3. Wrong audience rejected.
  4. alg=none rejected (forged header after normal encoding).
  5. Expired token rejected.
  6. TTL clamp to MAX (3600).
  7. Negative TTL raises ValueError.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from awaithumans.server.services.embed_token_service import (
    EmbedClaims,
    sign_embed_token,
    verify_embed_token,
)
from awaithumans.server.services.exceptions import InvalidEmbedTokenError
from awaithumans.utils.constants import (
    EMBED_TOKEN_MAX_TTL_SECONDS,
    EMBED_TOKEN_MIN_TTL_SECONDS,
)

_SECRET = "test-secret-key-for-embed-tokens"
_TASK_ID = "task_01HXYZ"
_SUB = "user@example.com"
_KIND = "end_user"
_ORIGIN = "https://app.example.com"


def _sign_default(**overrides: object) -> tuple[str, int]:
    """Helper: sign with sensible defaults, allowing field overrides."""
    kwargs: dict[str, object] = dict(
        secret=_SECRET,
        task_id=_TASK_ID,
        sub=_SUB,
        kind=_KIND,
        parent_origin=_ORIGIN,
        ttl_seconds=300,
    )
    kwargs.update(overrides)
    return sign_embed_token(**kwargs)  # type: ignore[arg-type]


# ── 1. Round-trip ──────────────────────────────────────────────────────────


def test_round_trip_returns_embed_claims() -> None:
    """sign then verify returns an EmbedClaims with the expected field values."""
    token, exp_unix = _sign_default()
    claims = verify_embed_token(token, secret=_SECRET)

    assert isinstance(claims, EmbedClaims)
    assert claims.task_id == _TASK_ID
    assert claims.sub == _SUB
    assert claims.kind == _KIND
    assert claims.parent_origin == _ORIGIN
    assert claims.exp == exp_unix
    assert claims.iat <= claims.exp
    assert len(claims.jti) > 0


# ── 2. Tampered signature rejected ────────────────────────────────────────


def test_tampered_signature_rejected() -> None:
    """Flipping a character in the signature segment must raise InvalidEmbedTokenError."""
    token, _ = _sign_default()
    header, payload, sig = token.rsplit(".", 2)
    # Flip a character near the START of the signature segment, not at
    # the end. base64url's final character can carry up to 2 "unused"
    # bits (HMAC-SHA256 = 256 bits encoded into 43 chars × 6 = 258
    # bits, with 2 surplus bits in the trailing char). Flips that only
    # change those surplus bits are no-ops on decode and the signature
    # stays valid — the test went flaky across Python versions
    # depending on what the last byte of the random HMAC happened to be.
    # Interior characters are always load-bearing.
    mid = len(sig) // 2
    bad_sig = sig[:mid] + ("A" if sig[mid] != "A" else "B") + sig[mid + 1 :]
    forged = f"{header}.{payload}.{bad_sig}"

    with pytest.raises(InvalidEmbedTokenError):
        verify_embed_token(forged, secret=_SECRET)


# ── 3. Wrong audience rejected ────────────────────────────────────────────


def test_wrong_audience_rejected() -> None:
    """A token signed with a different audience claim must raise InvalidEmbedTokenError."""
    import jwt as pyjwt

    now = int(time.time())
    payload = {
        "iss": "awaithumans",
        "aud": "wrong-audience",
        "iat": now,
        "exp": now + 300,
        "task_id": _TASK_ID,
        "sub": _SUB,
        "kind": _KIND,
        "parent_origin": _ORIGIN,
        "jti": "jti-test-wrong-aud",
    }
    bad_token = pyjwt.encode(payload, _SECRET, algorithm="HS256")

    with pytest.raises(InvalidEmbedTokenError):
        verify_embed_token(bad_token, secret=_SECRET)


# ── 4. alg=none rejected ──────────────────────────────────────────────────


def test_alg_none_rejected() -> None:
    """Forging a token with alg=none in the header must raise InvalidEmbedTokenError.

    Recipe: encode normally, extract header.payload.signature, replace header
    segment with base64url({"alg":"none","typ":"JWT"}), pass to verify_embed_token.
    PyJWT defends via the algorithms= allowlist.
    """
    token, _ = _sign_default()
    _header_b64, payload_b64, _sig = token.split(".")

    # Build a forged header with alg=none
    none_header = json.dumps({"alg": "none", "typ": "JWT"}).encode()
    # base64url encode without padding
    none_header_b64 = base64.urlsafe_b64encode(none_header).rstrip(b"=").decode()

    # Construct the forged token (unsigned — empty signature segment)
    forged = f"{none_header_b64}.{payload_b64}."

    with pytest.raises(InvalidEmbedTokenError):
        verify_embed_token(forged, secret=_SECRET)


# ── 5. Expired token rejected ─────────────────────────────────────────────


def test_expired_token_rejected() -> None:
    """A token with exp in the past (beyond leeway) must raise InvalidEmbedTokenError.

    Because sign_embed_token clamps TTL ≥ MIN, we can't produce an expired
    token via the public API. Instead: sign normally, re-encode with a past exp.
    """
    import jwt as pyjwt

    token, _ = _sign_default()
    # Decode without verification to get the raw payload
    raw = pyjwt.decode(token, options={"verify_signature": False})

    # Mutate exp to well in the past (beyond any leeway)
    raw["exp"] = int(time.time()) - 1000

    expired_token = pyjwt.encode(raw, _SECRET, algorithm="HS256")

    with pytest.raises(InvalidEmbedTokenError):
        verify_embed_token(expired_token, secret=_SECRET)


# ── 6. TTL clamp to MAX ───────────────────────────────────────────────────


def test_ttl_clamped_to_max() -> None:
    """A ttl_seconds above MAX must be clamped to EMBED_TOKEN_MAX_TTL_SECONDS."""
    oversized_ttl = EMBED_TOKEN_MAX_TTL_SECONDS + 9999
    _token, exp_unix = sign_embed_token(
        secret=_SECRET,
        task_id=_TASK_ID,
        sub=_SUB,
        kind=_KIND,
        parent_origin=_ORIGIN,
        ttl_seconds=oversized_ttl,
    )
    now = int(time.time())
    actual_ttl = exp_unix - now
    # Allow ±2s for test execution time
    assert actual_ttl <= EMBED_TOKEN_MAX_TTL_SECONDS + 2
    assert actual_ttl >= EMBED_TOKEN_MAX_TTL_SECONDS - 2


# ── 7. Negative TTL raises ValueError ────────────────────────────────────


def test_negative_ttl_raises_value_error() -> None:
    """A negative ttl_seconds must raise ValueError before any JWT is produced."""
    with pytest.raises(ValueError, match="ttl_seconds"):
        sign_embed_token(
            secret=_SECRET,
            task_id=_TASK_ID,
            sub=_SUB,
            kind=_KIND,
            parent_origin=_ORIGIN,
            ttl_seconds=-1,
        )


# ── Bonus: TTL clamp to MIN ───────────────────────────────────────────────


def test_ttl_clamped_to_min() -> None:
    """A ttl_seconds of 0 must be clamped up to EMBED_TOKEN_MIN_TTL_SECONDS."""
    _token, exp_unix = sign_embed_token(
        secret=_SECRET,
        task_id=_TASK_ID,
        sub=_SUB,
        kind=_KIND,
        parent_origin=_ORIGIN,
        ttl_seconds=0,
    )
    now = int(time.time())
    actual_ttl = exp_unix - now
    assert actual_ttl >= EMBED_TOKEN_MIN_TTL_SECONDS - 2
    assert actual_ttl <= EMBED_TOKEN_MIN_TTL_SECONDS + 2


# ── 8. Unsupported kind rejected ──────────────────────────────────────────


def test_unsupported_kind_rejected() -> None:
    """A token with kind='operator' must raise InvalidEmbedTokenError.

    Even if all other claims are valid, unsupported kinds are rejected
    during verification.
    """
    token, _ = _sign_default(kind="operator")

    with pytest.raises(InvalidEmbedTokenError):
        verify_embed_token(token, secret=_SECRET)


# ── 9. Missing required claim rejected ────────────────────────────────────


def test_token_with_missing_required_claim_rejected() -> None:
    """A token missing a required claim (e.g., iat) must raise InvalidEmbedTokenError.

    pyjwt.decode(options={'require': [...]}) enforces the check at decode
    time, routing through InvalidEmbedTokenError instead of crashing at
    int(decoded['iat']).
    """
    import jwt as pyjwt

    now = int(time.time())
    # Construct a token with all custom claims but missing 'iat' (a required claim).
    payload = {
        "iss": "awaithumans",
        "aud": "awaithumans-embed",
        "exp": now + 300,
        # Intentionally omit 'iat' — it's required.
        "task_id": _TASK_ID,
        "sub": _SUB,
        "kind": _KIND,
        "parent_origin": _ORIGIN,
        "jti": "jti-test-missing-iat",
    }
    token = pyjwt.encode(payload, _SECRET, algorithm="HS256")

    with pytest.raises(InvalidEmbedTokenError):
        verify_embed_token(token, secret=_SECRET)
