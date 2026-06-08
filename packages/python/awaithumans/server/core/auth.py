"""Dashboard auth — DB-backed users + HMAC session cookies.

Always on. First-run state (zero users in the DB) is handled by the
`/api/setup/*` routes which sit in the public-prefix list and gate
themselves on an in-memory bootstrap token.

Wire format: `cookie = base64url(hmac(body) || body)` where
`body = json({u: user_id, o: is_operator, e: expiry_unix})`. The HMAC
key is HKDF-derived from PAYLOAD_KEY with a channel-scoped salt, so
the same root key never signs two primitives.

Session validation is two-step:
- `verify_session(cookie)` checks HMAC, expiry, and payload shape —
  no DB hit. Returns a `SessionClaims` dataclass.
- Routes that need fresh user data read `request.state.auth_claims` and
  call the user service. The admin API gate (`core/admin_auth.py`)
  already handles operator-vs-bearer resolution.

An operator password change or row deletion doesn't invalidate
outstanding sessions until they expire. Acceptable for v1. Post-launch
we can add a `session_version` field and re-sign on password reset.

The middleware enforces auth before routes run:
- public paths (`/api/auth/*`, `/api/setup/*`, `/api/health`, static
  assets) skip the check
- a valid `Authorization: Bearer <ADMIN_API_TOKEN>` acts as a skeleton
  key for automation (CI, ops scripts)
- otherwise a valid session cookie is required, else 401
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from awaithumans.server.core.config import settings
from awaithumans.server.core.encryption import get_key
from awaithumans.utils.constants import (
    DASHBOARD_SESSION_COOKIE_NAME,
    DASHBOARD_SESSION_HKDF_INFO,
    DASHBOARD_SESSION_HKDF_SALT,
    DASHBOARD_SESSION_MAX_AGE_SECONDS,
    HMAC_SHA256_DIGEST_BYTES,
)

logger = logging.getLogger("awaithumans.server.core.auth")


# Paths that stay public even when auth is on:
# - /api/auth/*        — login, logout, introspection
# - /api/setup/*       — first-run bootstrap (gates itself on a token)
# - /api/health        — readiness probes
# - /api/version       — server version (ops + SDK compatibility probe)
# - /api/docs, /api/redoc, /api/openapi.json — FastAPI's auto-generated
#   API explorer + schema. Documenting the public API surface is the
#   whole point; the endpoints themselves are still bearer-gated.
# - slack/email action — signed by their own HMAC, no session needed
_PUBLIC_PREFIXES = (
    "/api/auth/",
    "/api/setup/",
    "/api/health",
    "/api/version",  # ops + SDK compatibility probe; no secrets exposed
    "/api/docs",
    "/api/redoc",
    "/api/openapi.json",
    "/api/channels/slack/oauth/",  # Slack-signed state gates these
    "/api/channels/slack/interactions",  # HMAC request signature gates this
    "/api/channels/slack/events",  # HMAC request signature gates this
    "/api/channels/email/action/",  # magic links are self-signed
    "/api/embed/",  # service-key auth via require_service_key dep
)


class InvalidSessionError(Exception):
    """Session cookie failed HMAC, expiry, or format validation."""


@dataclass(frozen=True)
class SessionClaims:
    """What the cookie tells us about the caller, without a DB hit.

    `user_id` is the stable ID; email and display_name aren't signed
    in to keep the cookie short and avoid stale display on rename.
    `is_operator` is baked in so the middleware can make coarse
    authz decisions (admin routes) without a DB query — fresh checks
    still happen in route-level deps when a mutation is about to run.
    """

    user_id: str
    is_operator: bool


# ─── Key derivation ─────────────────────────────────────────────────────


def _hmac_key() -> bytes:
    """Derive a 32-byte HMAC key from PAYLOAD_KEY via HKDF-SHA256."""
    return HKDF(
        algorithm=SHA256(),
        length=HMAC_SHA256_DIGEST_BYTES,
        salt=DASHBOARD_SESSION_HKDF_SALT,
        info=DASHBOARD_SESSION_HKDF_INFO,
    ).derive(get_key())


# ─── Cookie sign/verify ─────────────────────────────────────────────────


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def sign_session(*, user_id: str, is_operator: bool, ttl_seconds: int | None = None) -> str:
    """Produce a signed session cookie value."""
    ttl = ttl_seconds if ttl_seconds is not None else DASHBOARD_SESSION_MAX_AGE_SECONDS
    body = _canonical(
        {
            "u": user_id,
            "o": bool(is_operator),
            "e": int(time.time()) + ttl,
        }
    )
    mac = hmac.new(_hmac_key(), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac + body).decode().rstrip("=")


def verify_session(cookie: str) -> SessionClaims:
    """Decode + verify a session cookie. Raises `InvalidSessionError`
    on any failure. Does NOT touch the DB — caller should re-read the
    User row if freshness matters."""
    if not cookie:
        raise InvalidSessionError("empty cookie")

    padded = cookie + "=" * (-len(cookie) % 4)
    try:
        blob = base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise InvalidSessionError(f"not base64: {exc}") from exc

    if len(blob) < HMAC_SHA256_DIGEST_BYTES + 2:
        raise InvalidSessionError("too short")

    mac, body = blob[:HMAC_SHA256_DIGEST_BYTES], blob[HMAC_SHA256_DIGEST_BYTES:]
    expected = hmac.new(_hmac_key(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, mac):
        raise InvalidSessionError("signature mismatch")

    try:
        payload = json.loads(body)
        user_id = str(payload["u"])
        is_operator = bool(payload["o"])
        expires_at = int(payload["e"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidSessionError(f"malformed body: {exc}") from exc

    if time.time() > expires_at:
        raise InvalidSessionError("expired")

    return SessionClaims(user_id=user_id, is_operator=is_operator)


# ─── Middleware ─────────────────────────────────────────────────────────


def _is_public_path(path: str) -> bool:
    return any(path.startswith(p) for p in _PUBLIC_PREFIXES)


def _has_valid_admin_token(request: Request) -> bool:
    """Admin bearer token — automation escape hatch. Accepts either
    `Authorization: Bearer <token>` (standard) or `X-Admin-Token:
    <token>` (legacy header still used by the email identity CRUD)."""
    if not settings.ADMIN_API_TOKEN:
        return False

    supplied: str | None = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        supplied = auth_header.split(" ", 1)[1].strip()
    else:
        xadmin = request.headers.get("x-admin-token")
        if xadmin:
            supplied = xadmin

    if not supplied:
        return False
    return hmac.compare_digest(supplied, settings.ADMIN_API_TOKEN)


class DashboardAuthMiddleware(BaseHTTPMiddleware):
    """Gate the API behind a logged-in user or the admin bearer token."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path

        # Embed routes carry their own JWT-based auth (EmbedAuthMiddleware).
        # DashboardAuthMiddleware must not redirect or 401 embed requests —
        # the browser fetches /embed/<taskId> anonymously and the JS bundle
        # then reads the URL fragment to obtain the embed token.
        if path.startswith("/embed/"):
            return await call_next(request)

        # Non-API requests (static assets, /docs, etc.) pass through.
        # The dashboard enforces its own redirect via middleware.ts.
        if not path.startswith("/api/"):
            return await call_next(request)

        if _is_public_path(path):
            return await call_next(request)

        # Embed-bearer caller — EmbedAuthMiddleware already verified the
        # JWT and stamped `request.state.embed_ctx`. Routes that accept
        # embed access (currently `/api/tasks/{id}` and
        # `/api/tasks/{id}/complete`) check this themselves.
        if getattr(request.state, "embed_ctx", None) is not None:
            return await call_next(request)

        if _has_valid_admin_token(request):
            # Bearer-token caller — mark the request so downstream deps
            # can distinguish "logged-in operator" from "automation."
            request.state.auth_admin_token = True
            return await call_next(request)

        cookie = request.cookies.get(DASHBOARD_SESSION_COOKIE_NAME)
        if cookie:
            try:
                claims = verify_session(cookie)
                request.state.auth_claims = claims
                return await call_next(request)
            except InvalidSessionError as exc:
                logger.info("Rejected session cookie: %s", exc)

        return JSONResponse(
            {"detail": "Authentication required."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
