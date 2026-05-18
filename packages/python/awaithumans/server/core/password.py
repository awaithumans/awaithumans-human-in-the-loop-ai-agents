"""Password hashing — argon2id via argon2-cffi.

Argon2id is the OWASP-recommended default for new systems as of 2024
(memory-hard, side-channel-resistant, winner of the Password Hashing
Competition). The defaults in `argon2-cffi` match the minimum
parameters from RFC 9106; we don't tune them here.

Usage:

    from awaithumans.server.core.password import hash_password, verify_password
    h = hash_password("s3cret")
    verify_password("s3cret", h)        # True
    verify_password("wrong", h)         # False

Also exposes `dummy_verify()` for timing-equalizing the login path when
a user isn't found — argon2's ~100ms cost is easily measurable, and
fast-failing on unknown emails would leak account existence.
"""

from __future__ import annotations

import contextlib
import logging

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

logger = logging.getLogger("awaithumans.server.core.password")

# Module-level singleton — `PasswordHasher` is threadsafe and cheap to
# keep around. Expensive to construct on every call.
_hasher = PasswordHasher()

# Pre-computed dummy hash. Generated once at import; `dummy_verify`
# verifies any password against this so the unknown-user login path
# costs the same CPU as the known-user path. The hash is for a random
# unknown string — no real user can ever match it.
_DUMMY_HASH = _hasher.hash("*timing-equalization-sentinel*")


def hash_password(password: str) -> str:
    """Return an argon2id hash string (PHC format, fully self-describing)."""
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time verify. Returns False on mismatch or malformed hash;
    never raises on bad input — callers just see "wrong password." """
    try:
        return _hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        return False
    except Exception as exc:  # malformed hash, unexpected algorithm, etc.
        logger.warning("password verify failed (treated as mismatch): %s", exc)
        return False


def dummy_verify(password: str) -> None:
    """Run argon2 verify against a pre-computed dummy hash.

    Use on the "user not found" branch of login so unknown emails cost
    the same CPU time as known ones. Without this, a timing probe can
    distinguish registered accounts by response latency (argon2 adds
    ~100ms per attempt, an easily-measurable delta).

    Result is discarded — this function only exists to spend CPU.
    """
    # Any outcome is fine; we just wanted the CPU spend.
    with contextlib.suppress(Exception):
        _hasher.verify(_DUMMY_HASH, password)
