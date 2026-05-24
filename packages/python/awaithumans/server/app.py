"""FastAPI application factory.

All configuration, middleware, and error handling live in server/core/.
This file only wires them together.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response

from awaithumans import __version__
from awaithumans.server.core.auth import DashboardAuthMiddleware
from awaithumans.server.core.channel_config_validator import (
    validate_channel_config,
)
from awaithumans.server.core.config import settings, unknown_env_keys
from awaithumans.server.core.dashboard_static import DashboardStaticFiles
from awaithumans.server.core.embed_auth import EmbedAuthMiddleware
from awaithumans.server.core.encryption import (
    EncryptionKeyError,
    EncryptionNotConfiguredError,
    get_key,
)
from awaithumans.server.core.exceptions import exception_handlers
from awaithumans.server.core.logging_config import setup_logging
from awaithumans.server.core.middleware import RequestIDMiddleware
from awaithumans.server.db.connection import close_db, init_db
from awaithumans.server.routes import (
    auth,
    email,
    health,
    setup,
    slack,
    stats,
    status,
    tasks,
    users,
    version,
    webhook_deliveries,
)
from awaithumans.server.routes import embed as embed_routes
from awaithumans.server.services.embed_token_service import parse_origin_allowlist
from awaithumans.server.services.timeout_scheduler import run_timeout_scheduler
from awaithumans.server.services.user_service import count_users
from awaithumans.server.services.webhook_scheduler import run_webhook_scheduler

logger = logging.getLogger("awaithumans.server")


# ── Embed security middleware ─────────────────────────────────────────────────


class EmbedResponseHeadersMiddleware(BaseHTTPMiddleware):
    """Apply strict security headers to /embed/* responses (spec §5.7).

    Runs on every response whose path starts with ``/embed``.  Adds:

    * ``Content-Security-Policy`` — locks down what the embedded page may
      load.  ``frame-ancestors`` is built from ``settings.EMBED_PARENT_ORIGINS``
      so only the operator's listed origins may host the iframe.
    * ``Referrer-Policy: no-referrer`` — prevents the task URL leaking to
      third-party resources the page happens to load.
    * ``Permissions-Policy`` — denies access to browser APIs the embed frame
      has no business touching.
    * ``X-Content-Type-Options: nosniff`` — defence-in-depth against MIME
      sniffing of the JS/CSS bundles.
    """

    async def dispatch(self, request: Request, call_next: object) -> Response:
        response: Response = await call_next(request)  # type: ignore[arg-type]
        if not request.url.path.startswith("/embed"):
            return response

        allowlist = parse_origin_allowlist(settings.EMBED_PARENT_ORIGINS)
        ancestors = " ".join(allowlist) if allowlist else "'none'"
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-src 'none'; "
            f"frame-ancestors {ancestors}"
        )
        response.headers["Content-Security-Policy"] = csp
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=()"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown lifecycle."""
    # Fail fast on a misconfigured PAYLOAD_KEY (bad length, bad base64,
    # missing entirely). The key is needed for every signed-session
    # cookie, but `get_key()` was only invoked lazily on the first
    # request that needed it — which for new operators is the signup
    # POST. The lazy failure surfaced as a generic
    # 500 INTERNAL_ERROR with the helpful EncryptionKeyError message
    # buried in server logs and the operator left with a half-created
    # user row, no session, and no path forward (#140).
    #
    # Calling get_key() here surfaces the same error message at boot,
    # before uvicorn accepts traffic — so the operator sees the
    # remediation hint on the very first run, not after their first
    # failed signup.
    try:
        get_key()
    except (EncryptionKeyError, EncryptionNotConfiguredError) as exc:
        logger.error(
            "Refusing to start: AWAITHUMANS_PAYLOAD_KEY is misconfigured. %s",
            exc,
        )
        raise

    await init_db()
    logger.info("Database initialized")

    # Capture whether this is first-run inside the lifespan (the DB
    # session is ready here). The banner itself prints after uvicorn
    # logs "Application startup complete" so it's the LAST thing the
    # operator sees — not buried between alembic migrations and the
    # "Running on http://..." line.
    setup_url = await _first_run_setup_url()

    scheduler_task = asyncio.create_task(run_timeout_scheduler())
    webhook_task = asyncio.create_task(run_webhook_scheduler())
    banner_task: asyncio.Task[None] | None = None
    if setup_url:
        banner_task = asyncio.create_task(_print_banner_after_startup(setup_url))

    yield

    if banner_task and not banner_task.done():
        banner_task.cancel()
        with suppress(asyncio.CancelledError):
            await banner_task
    scheduler_task.cancel()
    webhook_task.cancel()
    with suppress(asyncio.CancelledError):
        await scheduler_task
    with suppress(asyncio.CancelledError):
        await webhook_task
    await close_db()
    logger.info("Server shut down")


async def _first_run_setup_url() -> str | None:
    """Return the setup URL if the DB has no users (first run), else None.

    Generating the bootstrap token here keeps the token inside the
    server process from the moment first-run is detected.
    """
    from awaithumans.server.core import bootstrap
    from awaithumans.server.db.connection import get_async_session_factory

    factory = get_async_session_factory()
    async with factory() as session:
        n = await count_users(session)

    if n != 0:
        return None

    token = bootstrap.ensure_token()
    return f"{settings.PUBLIC_URL.rstrip('/')}/setup?token={token}"


async def _print_banner_after_startup(setup_url: str) -> None:
    """Wait briefly so uvicorn's startup-complete log lands first,
    then print the setup banner. Short sleep is intentional — we want
    the banner to be the last thing the operator sees when reading
    the startup output."""
    from awaithumans.server.core import bootstrap

    await asyncio.sleep(0.4)
    bootstrap.log_setup_banner(setup_url)


def create_app(*, serve_dashboard: bool = True) -> FastAPI:
    """Create the FastAPI application.

    Args:
        serve_dashboard: If True, serve the pre-built dashboard static files
            at the root path. Set to False when running the dashboard separately
            in development mode.
    """
    # ── Logging ──────────────────────────────────────────────────────
    setup_logging(settings.LOG_LEVEL)

    # ── .env hygiene ────────────────────────────────────────────────
    # SDK-side vars (AWAITHUMANS_URL, etc.) live under the same prefix
    # as server vars and used to crash the server on boot when present
    # in a shared `.env`. We now silently ignore them — but a typo like
    # `AWAITHUMANS_SLAK_BOT_TOKEN` would be ignored too, so we surface
    # any unrecognized `AWAITHUMANS_*` keys as a WARNING. Cheap to scan,
    # only runs once at app creation.
    _unknown = unknown_env_keys()
    if _unknown:
        logger.warning(
            "Found %d AWAITHUMANS_* key(s) in .env that the server does not "
            "consume: %s. These are ignored. Likely cause: SDK-side vars in a "
            "shared .env (see docs/self-hosting/configuration#two-namespaces). "
            "If one is a typo of a real server var, fix it and restart.",
            len(_unknown),
            ", ".join(sorted(_unknown)),
        )

    # ── Channel-config hygiene ──────────────────────────────────────
    # If a channel is partly configured (e.g. EMAIL_TRANSPORT=smtp set
    # but no SMTP_HOST), warn at boot listing the missing env vars.
    # Pre-banner-PR, operators only found out at first send when the
    # task silently failed; this is the front-line check.
    validate_channel_config(settings)

    # ── Production safety checks ─────────────────────────────────────
    # HTTPS is required in production because the server handles Slack OAuth
    # tokens and bearer credentials that must not transit in cleartext.
    if settings.is_production and not settings.PUBLIC_URL.startswith("https://"):
        logger.error(
            "SECURITY: ENVIRONMENT=production but PUBLIC_URL is not HTTPS "
            "(value=%s). Tokens and OAuth state will transit in cleartext. "
            "Set PUBLIC_URL to an https:// URL.",
            settings.PUBLIC_URL,
        )

    # PUBLIC_URL must be a base URL (scheme + host[:port]) — no path.
    # A common misconfiguration: pasting the full Slack OAuth callback
    # URL (`https://example.com/api/channels/slack/oauth/callback`) into
    # PUBLIC_URL. That breaks every dashboard click-through (the Slack
    # message's "Open in dashboard" link becomes
    # `https://example.com/api/channels/slack/oauth/callback/task?id=…`)
    # and the OAuth callback redirects to the wrong place. Fail-fast at
    # boot with a concrete fix instead of letting the operator
    # debug 404s.
    _validate_public_url(settings.PUBLIC_URL)

    # OAuth stores bot tokens in the DB. Those tokens are encrypted at rest
    # via server/core/encryption.py, which needs PAYLOAD_KEY. Without the
    # key we'd either silently fall back to plaintext (unsafe) or crash on
    # first install (surprising). Fail fast at boot instead.
    if settings.SLACK_CLIENT_ID and not settings.PAYLOAD_KEY:
        raise RuntimeError(
            "Slack OAuth is enabled (SLACK_CLIENT_ID is set) but PAYLOAD_KEY "
            "is not. Tokens cannot be encrypted at rest without it. Generate "
            "one with: python -c 'import secrets; print(secrets.token_urlsafe(32))' "
            "and set AWAITHUMANS_PAYLOAD_KEY."
        )

    # Dashboard auth signs session cookies with an HKDF-derived key
    # from PAYLOAD_KEY. Without it we'd either silently fall back to
    # an insecure default (no) or crash on first login (surprising).
    # Auth is always on in v1, so this is always required.
    if not settings.PAYLOAD_KEY:
        raise RuntimeError(
            "AWAITHUMANS_PAYLOAD_KEY is required — session cookies and "
            "encrypted-at-rest columns both derive their keys from it. "
            "Generate one with: python -c 'import secrets; "
            "print(secrets.token_urlsafe(32))'"
        )

    # CORS sanity check. Below we set `allow_credentials = (origins
    # != "*")`, which means the moment an operator narrows the origin
    # list at all, credentials flip on. If the narrowed list contains
    # http:// origins or stray wildcards, that's a session-ride
    # vector — credentials cross-origin to an attacker-controlled
    # site. Refuse to start instead of guessing.
    _validate_cors_origins(settings.cors_origin_list)

    # ── App ──────────────────────────────────────────────────────────
    # Docs paths live under /api/* to match the rest of the URL surface
    # (every other backend endpoint is /api/tasks, /api/auth, etc.).
    # The docs page at docs/api/overview.mdx already promises /api/docs
    # and /api/openapi.json — the FastAPI defaults of /docs were a
    # framework leak we never overrode.
    #
    # Auth bypass for these paths is in `core/auth.py:_PUBLIC_PREFIXES`
    # so the Swagger UI loads without a session — same posture as the
    # previous /docs path. The API endpoints it documents are still
    # bearer-gated; the schema itself is intentionally public for
    # client-code-gen.
    app = FastAPI(
        title="awaithumans",
        description="The human layer for AI agents.",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # ── Exception handlers ───────────────────────────────────────────
    for exc_class, handler in exception_handlers.items():
        app.add_exception_handler(exc_class, handler)

    # ── Middleware (order matters — last added = first executed) ──────
    # Auth runs after CORS (so preflight OPTIONS still works) and after
    # RequestID (so failed-auth responses carry a request ID).
    # EmbedResponseHeadersMiddleware runs after DashboardAuthMiddleware so
    # it can annotate the final response with security headers even if the
    # auth layer short-circuits (the auth skip for /embed/* means the response
    # comes from whatever handler answers, and the headers are appended here).
    app.add_middleware(DashboardAuthMiddleware)
    app.add_middleware(EmbedResponseHeadersMiddleware)
    app.add_middleware(
        EmbedAuthMiddleware,
        secret_provider=lambda: settings.EMBED_SIGNING_SECRET,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=settings.CORS_ORIGINS != "*",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIDMiddleware)

    # ── Routes ───────────────────────────────────────────────────────
    app.include_router(auth.router, prefix="/api")
    app.include_router(status.router, prefix="/api")
    app.include_router(stats.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(health.router, prefix="/api")
    app.include_router(version.router, prefix="/api")
    app.include_router(slack.router, prefix="/api")
    app.include_router(email.router, prefix="/api")
    app.include_router(users.router, prefix="/api")
    app.include_router(setup.router, prefix="/api")
    app.include_router(webhook_deliveries.router, prefix="/api")
    app.include_router(embed_routes.router)

    # ── Dashboard static files ───────────────────────────────────────
    # The bundled dashboard lives inside the package
    # (`awaithumans/dashboard_dist/`) so hatchling includes it in the
    # wheel. `DashboardStaticFiles` extends Starlette's `StaticFiles`
    # with a `<path>.html` fallback so Next's static-export output
    # (`/setup` → `setup.html`, `/settings` → `settings.html`, etc.)
    # resolves on direct URL hits, not just client-side navigation.
    if serve_dashboard:
        dashboard_dist = Path(__file__).parent.parent / "dashboard_dist"
        if dashboard_dist.exists():
            app.mount(
                "/",
                DashboardStaticFiles(directory=str(dashboard_dist), html=True),
                name="dashboard",
            )
        else:
            logger.info(
                "Dashboard not bundled — skipping static mount. "
                "Run scripts/build-bundled.sh to include it in the wheel."
            )

    logger.info(
        "App created (environment=%s, cors=%s, dashboard=%s)",
        settings.ENVIRONMENT,
        settings.CORS_ORIGINS,
        serve_dashboard,
    )

    return app


def _validate_cors_origins(origins: list[str]) -> None:
    """Reject CORS configurations that would expose credentials to
    untrusted origins.

    The combination `allow_credentials=True` + a too-loose origin list
    is the classic session-ride trap. We allow:

      - `["*"]` exactly (credentials are forced OFF in app.py for
        this case, so any origin can read but nothing carries cookies)
      - any number of explicit https:// origins (hostname or IP, with
        an optional port)
      - http://localhost or http://127.0.0.1 (with optional port) for
        dev-mode dashboards. Pure dev affordance — production
        deployments should run with HTTPS PUBLIC_URL anyway.

    Anything else (mixed `*` with explicit, plain http:// non-local,
    malformed strings) makes the server refuse to start with an
    actionable message."""
    if origins == ["*"]:
        return

    https_re = re.compile(r"^https://[A-Za-z0-9.\-]+(:\d+)?$")
    local_http_re = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")

    for origin in origins:
        if origin == "*":
            raise RuntimeError(
                "AWAITHUMANS_CORS_ORIGINS contains '*' alongside explicit "
                "origins. Browsers reject this combination, and our auth "
                "middleware would flip credentials ON because the list "
                "isn't a bare '*'. Use either '*' alone (no credentials) "
                "or a fully-explicit https:// list."
            )
        if https_re.match(origin) or local_http_re.match(origin):
            continue
        raise RuntimeError(
            f"AWAITHUMANS_CORS_ORIGINS contains an unsafe origin: '{origin}'. "
            "Plain http:// origins outside localhost would carry session "
            "cookies in cleartext to whichever site the operator listed. "
            "Use https:// only (or http://localhost / http://127.0.0.1 "
            "for local dev)."
        )


def _validate_public_url(url: str) -> None:
    """Reject `PUBLIC_URL` values that include a path beyond `/`.

    The contract: `PUBLIC_URL` is the base of every URL the server
    constructs (dashboard click-throughs, OAuth redirects, magic
    links). Code does `f"{settings.PUBLIC_URL.rstrip('/')}/task?id=…"`
    and friends. If `PUBLIC_URL` is, say,
    `https://example.com/api/channels/slack/oauth/callback`, every
    constructed URL stacks the path and breaks. Operators usually
    hit this when they paste a full callback URL into the env var
    instead of just the host.

    Acceptable shapes:
      https://host
      https://host:port
      https://host/         (trailing slash tolerated)
      http://localhost
      http://localhost:3001

    Rejected:
      https://host/api/...
      https://host/anything-besides-a-trailing-slash
      missing scheme
      empty
    """
    if not url:
        raise RuntimeError(
            "AWAITHUMANS_PUBLIC_URL is unset. Set it to the base URL the "
            "dashboard is reachable at, e.g. http://localhost:3001 in dev "
            "or https://reviews.your-company.com in production."
        )

    valid = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d+)?/?$")
    if not valid.match(url):
        raise RuntimeError(
            f"AWAITHUMANS_PUBLIC_URL='{url}' is not a base URL — it must "
            "be scheme + host + optional port, with no path. A common "
            "mistake is pasting the full Slack OAuth callback URL "
            "(`/api/channels/slack/oauth/callback`) into this variable; "
            "use just the host portion. Examples:\n"
            "  http://localhost:3001\n"
            "  https://reviews.your-company.com\n"
            "  https://abcd1234.ngrok-free.app"
        )
