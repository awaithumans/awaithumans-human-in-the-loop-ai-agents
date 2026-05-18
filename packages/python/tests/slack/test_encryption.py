"""At-rest encryption — primitives + transparent column encryption.

Encryption is AES-256-GCM with a random 96-bit nonce per write and a
key-version prefix. Tests here prove:

- plaintext is never on the wire (ciphertext != plaintext)
- each encrypt produces a fresh ciphertext (nonce randomness)
- wrong key, tampered ciphertext, bad version, and missing key all fail
- the `EncryptedString` column type round-trips transparently through
  the DB, and a raw SQL peek shows ciphertext, not plaintext
"""

from __future__ import annotations

import base64
import secrets
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from cryptography.exceptions import InvalidTag
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from awaithumans.server.core.config import settings
from awaithumans.server.core.encryption import (
    EncryptionKeyError,
    EncryptionNotConfiguredError,
    decrypt_str,
    encrypt_str,
    reset_key_cache,
)

# Register models so create_all picks up the table.
from awaithumans.server.db.models import (  # noqa: F401
    AuditEntry,
    SlackInstallation,
    Task,
)
from awaithumans.server.services.slack_installation_service import (
    get_installation,
    upsert_installation,
)

# A valid PAYLOAD_KEY is installed per-test by the autouse fixture in
# tests/slack/conftest.py. Tests below that need a DIFFERENT key state
# (wrong key, missing key, short key) mutate settings.PAYLOAD_KEY
# locally and the conftest teardown restores the original afterward.


# ─── Primitive ──────────────────────────────────────────────────────────


def test_roundtrip() -> None:
    ct = encrypt_str("xoxb-the-secret-token")
    assert ct != "xoxb-the-secret-token"
    assert decrypt_str(ct) == "xoxb-the-secret-token"


def test_ciphertext_is_fresh_each_call() -> None:
    """Nonce randomness: same plaintext → different ciphertexts."""
    assert encrypt_str("x") != encrypt_str("x")


def test_wrong_key_fails() -> None:
    ct = encrypt_str("payload")
    settings.PAYLOAD_KEY = secrets.token_urlsafe(32)  # a different valid key
    reset_key_cache()
    with pytest.raises(InvalidTag):
        decrypt_str(ct)


def test_tampered_ciphertext_raises() -> None:
    ct = encrypt_str("payload")
    raw = bytearray(base64.b64decode(ct))
    # Flip a bit in the ciphertext/tag region.
    raw[-1] ^= 0x01
    tampered = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(InvalidTag):
        decrypt_str(tampered)


def test_truncated_ciphertext_raises() -> None:
    ct = encrypt_str("payload")
    raw = base64.b64decode(ct)
    truncated = base64.b64encode(raw[:5]).decode()
    with pytest.raises(EncryptionKeyError):
        decrypt_str(truncated)


def test_bad_base64_raises() -> None:
    with pytest.raises(EncryptionKeyError):
        decrypt_str("!!!not base64!!!")


def test_wrong_key_version_raises() -> None:
    """Ciphertext written with a future key version must not be silently
    decrypted — key rotation is intentional and needs a real registry."""
    ct = encrypt_str("payload")
    raw = bytearray(base64.b64decode(ct))
    raw[0] = 0x99  # unknown version byte
    bogus = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(EncryptionKeyError):
        decrypt_str(bogus)


def test_missing_key_raises() -> None:
    settings.PAYLOAD_KEY = None
    reset_key_cache()
    with pytest.raises(EncryptionNotConfiguredError):
        encrypt_str("x")


def test_malformed_key_raises() -> None:
    settings.PAYLOAD_KEY = "!!!not base64!!!"
    reset_key_cache()
    with pytest.raises(EncryptionKeyError):
        encrypt_str("x")


def test_short_key_raises() -> None:
    """Key that decodes to fewer than 32 bytes is rejected at first use."""
    settings.PAYLOAD_KEY = base64.b64encode(b"short").decode()
    reset_key_cache()
    with pytest.raises(EncryptionKeyError):
        encrypt_str("x")


# ─── Transparent column encryption ──────────────────────────────────────


@pytest_asyncio.fixture
async def encrypted_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_bot_token_stored_encrypted_and_read_plain(
    encrypted_session,
) -> None:
    """Raw SQL shows ciphertext; service reads plaintext."""
    await upsert_installation(
        encrypted_session,
        team_id="T123",
        team_name="Acme",
        bot_token="xoxb-PLAIN-SENSITIVE",
        bot_user_id="U_BOT",
        scopes="chat:write",
    )

    # Read via the service — transparent decryption gives us plaintext.
    fetched = await get_installation(encrypted_session, "T123")
    assert fetched is not None
    assert fetched.bot_token == "xoxb-PLAIN-SENSITIVE"

    # Read via raw SQL — what's actually on disk — must NOT contain the token.
    result = await encrypted_session.execute(
        text("SELECT bot_token FROM slack_installations WHERE team_id = :tid"),
        {"tid": "T123"},
    )
    raw_value = result.scalar_one()
    assert raw_value != "xoxb-PLAIN-SENSITIVE"
    assert "xoxb" not in raw_value
    # Ciphertext is base64, so alphanumeric + `+/=`
    assert len(raw_value) > len("xoxb-PLAIN-SENSITIVE")


@pytest.mark.asyncio
async def test_decrypt_with_wrong_key_fails(encrypted_session) -> None:
    """Rotating the key without a registry breaks existing rows — loudly."""
    await upsert_installation(
        encrypted_session,
        team_id="T1",
        team_name="A",
        bot_token="xoxb-token",
        bot_user_id="U",
        scopes="chat:write",
    )
    # Rotate the key in place (no migration).
    settings.PAYLOAD_KEY = secrets.token_urlsafe(32)
    reset_key_cache()
    with pytest.raises(InvalidTag):
        await get_installation(encrypted_session, "T1")
