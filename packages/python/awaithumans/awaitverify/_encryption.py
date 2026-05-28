"""Client-side AES-256-GCM encryption for AwaitVerify fragments.

The SDK receives a 32-byte plaintext DEK in the `/uploads` response,
encrypts each fragment with a fresh nonce, and uploads the
ciphertext. The server stores only the wrapped DEK; on reviewer
submit it destroys that wrapped DEK and the fragments become
permanently undecryptable.

Wire format: nonce(12) || ciphertext_and_tag — the standard AES-GCM
concatenation the `cryptography` library produces.
"""

from __future__ import annotations

import os
from typing import Any

from awaithumans.awaitverify.errors import VerifyDepsMissingError

_GCM_NONCE_LENGTH_BYTES = 12
_DEK_LENGTH_BYTES = 32


def _require_aesgcm() -> Any:
    """Import AESGCM lazily so the base SDK does not depend on it."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

        return AESGCM
    except ImportError as exc:
        raise VerifyDepsMissingError("cryptography") from exc


def encrypt_fragment(plaintext: bytes, dek: bytes) -> bytes:
    """Encrypt a single fragment with the DEK. Returns nonce||ciphertext||tag.

    Each fragment uses a fresh random nonce so the (nonce, key) pair
    never repeats across fragments encrypted with the same DEK.
    """
    if len(dek) != _DEK_LENGTH_BYTES:
        raise ValueError(f"DEK must be exactly {_DEK_LENGTH_BYTES} bytes (got {len(dek)})")
    aesgcm_cls = _require_aesgcm()
    aesgcm = aesgcm_cls(dek)
    nonce = os.urandom(_GCM_NONCE_LENGTH_BYTES)
    ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data=None)
    return nonce + ciphertext
