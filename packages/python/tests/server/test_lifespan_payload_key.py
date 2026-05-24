"""Regression: PAYLOAD_KEY must be validated at lifespan startup.

Prior to #140, `get_key()` ran lazily on the first signed-cookie
request (signup, login). A wrong-length key let the server start
cleanly, accept the signup POST, persist the user row to DB, and then
raise `EncryptionKeyError` inside `sign_session()` — caught by the
catch-all handler and returned as a generic 500 INTERNAL_ERROR. The
operator was left with a half-created user, no session cookie, and
no recovery path.

These tests assert the lifespan now fails fast with the original
EncryptionKeyError message visible.
"""

from __future__ import annotations

import base64
import secrets

import pytest

from awaithumans.server.app import lifespan, create_app
from awaithumans.server.core import encryption
from awaithumans.server.core.config import settings


@pytest.fixture
def _restore_payload_key() -> None:
    """Snapshot + restore PAYLOAD_KEY around a test that mutates it."""
    original = settings.PAYLOAD_KEY
    yield
    settings.PAYLOAD_KEY = original
    encryption.reset_key_cache()


@pytest.mark.asyncio
async def test_lifespan_refuses_bad_length_key(_restore_payload_key: None) -> None:
    """A key that decodes to fewer than 32 bytes must abort lifespan startup.

    The classic footgun: `openssl rand -hex 16` yields 32 chars which
    base64-decode to 24 bytes — invalid as a 32-byte symmetric key but
    the wrong shape was easy to ship by accident.
    """
    settings.PAYLOAD_KEY = base64.urlsafe_b64encode(secrets.token_bytes(24)).rstrip(b"=").decode()
    encryption.reset_key_cache()

    app = create_app()
    with pytest.raises(encryption.EncryptionKeyError) as excinfo:
        async with lifespan(app):
            pass
    # The original helpful message must reach the operator unmodified.
    assert "32 bytes" in str(excinfo.value)
    assert "token_urlsafe(32)" in str(excinfo.value)


def test_create_app_refuses_unset_key(_restore_payload_key: None) -> None:
    """An unset key is caught even earlier — at create_app() construction —
    by a pre-existing check. Our lifespan-time validator is complementary:
    it catches BAD-FORMAT keys that pass the existence check at app build
    but fail the decode-and-length check at use time. Documenting both
    layers here so neither regresses.
    """
    settings.PAYLOAD_KEY = None  # type: ignore[assignment]
    encryption.reset_key_cache()

    with pytest.raises(RuntimeError) as excinfo:
        create_app()
    assert "AWAITHUMANS_PAYLOAD_KEY is required" in str(excinfo.value)
    assert "token_urlsafe(32)" in str(excinfo.value)


@pytest.mark.asyncio
async def test_lifespan_refuses_short_key(_restore_payload_key: None) -> None:
    """A SHORT key (decodes to fewer than 32 bytes) must abort lifespan.

    This is the most common real-world miss-shape because base64-ish
    strings of any length will decode successfully, just to the wrong
    byte count. The previous, lazy validation only surfaced this
    failure inside the first signed-session request.
    """
    # 12 bytes of entropy → ~16 chars of base64 → still parses as base64
    # but fails the 32-byte length check.
    settings.PAYLOAD_KEY = base64.urlsafe_b64encode(secrets.token_bytes(12)).rstrip(b"=").decode()
    encryption.reset_key_cache()

    app = create_app()
    with pytest.raises(encryption.EncryptionKeyError) as excinfo:
        async with lifespan(app):
            pass
    assert "32 bytes" in str(excinfo.value)
    assert "token_urlsafe(32)" in str(excinfo.value)


@pytest.mark.asyncio
async def test_lifespan_accepts_valid_key(_restore_payload_key: None) -> None:
    """A correctly-sized 32-byte URL-safe-base64 key must pass.

    Counter-test for the negative ones above: makes sure the new
    check isn't over-eager and refusing valid configs.
    """
    settings.PAYLOAD_KEY = secrets.token_urlsafe(32)
    encryption.reset_key_cache()

    app = create_app()
    # Should enter and exit the context without raising.
    async with lifespan(app):
        pass
