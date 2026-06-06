"""Server configuration — all settings from environment variables.

Usage:
    from awaithumans.server.core.config import settings

All config is read from env vars with sensible defaults for development.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

logger = logging.getLogger("awaithumans.server.core.config")


class Settings(BaseSettings):
    """Application settings, loaded from environment variables."""

    # ── Server ───────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 3001
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # ── Database ─────────────────────────────────────────────────────
    DATABASE_URL: str | None = None
    DB_PATH: str = ".awaithumans/dev.db"
    # Operator acknowledgement that the SQLite path lives on durable
    # storage (or that data loss is acceptable). Suppresses the
    # production-SQLite warning in `server/app.py` lifespan. Default
    # False because the failure mode it warns about — silent data loss
    # on container restart when the runtime ignores VOLUME — is severe
    # and not detectable from inside the process.
    ALLOW_EPHEMERAL_DB: bool = False

    # ── Dashboard auth ────────────────────────────────────────────────
    # Auth is always on. First-run state (empty users table) is handled
    # by the /api/setup bootstrap flow; see server/core/bootstrap.py.
    # All /api/* routes except the public prefix list require a valid
    # session cookie or the admin bearer token.

    # ── CORS ──────────────────────────────────────────────────────────
    CORS_ORIGINS: str = "*"  # Comma-separated list, or "*" for all

    # ── Email channel ────────────────────────────────────────────────
    # Server-wide default identity for email notifications. Per-task
    # `notify=["email+acme-prod:alice@..."]` overrides this.
    EMAIL_TRANSPORT: str | None = None  # "resend" | "smtp" | "logging" | "noop"
    EMAIL_FROM: str | None = None  # "notifications@acme.com"
    EMAIL_FROM_NAME: str | None = None  # "Acme Tasks"
    EMAIL_REPLY_TO: str | None = None
    # Resend transport.
    RESEND_KEY: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AWAITHUMANS_RESEND_KEY",
            "RESEND_API_KEY",
        ),
    )
    # SMTP transport.
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_USE_TLS: bool = False  # Implicit TLS (port 465). Rare.
    SMTP_START_TLS: bool = True  # STARTTLS on port 587. Most common.

    # Admin API token gates identity-management endpoints
    # (POST/DELETE /api/channels/email/identities, etc.).
    # Without this set, admin endpoints return 503.
    ADMIN_API_TOKEN: str | None = None

    # ── Notifications ────────────────────────────────────────────────
    SLACK_WEBHOOK: str | None = None
    # Static bot token for SINGLE-WORKSPACE self-hosted setups. Leave
    # unset and use CLIENT_ID/CLIENT_SECRET below for multi-workspace.
    SLACK_BOT_TOKEN: str | None = None
    SLACK_SIGNING_SECRET: str | None = None
    # OAuth credentials for MULTI-WORKSPACE distribution. When all three
    # (CLIENT_ID, CLIENT_SECRET, INSTALL_TOKEN) are set, the OAuth install
    # flow is enabled at /api/channels/slack/oauth/start.
    SLACK_CLIENT_ID: str | None = None
    SLACK_CLIENT_SECRET: str | None = None
    # Operator-only shared secret required to initiate an install.
    # Without this, any visitor who knows PUBLIC_URL could install their
    # own workspace into the server and receive notifications. Generate
    # a high-entropy value (e.g. `openssl rand -hex 32`) and share it
    # only with authorized admins. REQUIRED when OAuth is enabled.
    SLACK_INSTALL_TOKEN: str | None = None
    # Scopes requested during OAuth install. Override only if you need a
    # tighter or broader scope set than the default manifest.
    SLACK_OAUTH_SCOPES: str | None = None

    # ── Public URLs ──────────────────────────────────────────────────
    # Used when building link-out URLs in Slack/email so humans can
    # click through to the dashboard. In production, this is the
    # HTTPS URL of the server (which also serves the dashboard).
    PUBLIC_URL: str = "http://localhost:3001"

    # ── Verification ─────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str | None = None

    # ── Azure OpenAI (demo extractor) ────────────────────────────────
    # The AwaitVerify landing demo extractor calls Azure OpenAI rather
    # than Anthropic so the founder can route demo spend through their
    # Azure deployment quota. The deployment name is what the OpenAI
    # SDK calls the `model` parameter when talking to Azure.
    AZURE_OPENAI_ENDPOINT: str | None = None
    AZURE_OPENAI_DEPLOYMENT: str | None = None
    # Optional separate (and typically faster + cheaper) deployment for
    # the demo schema proposer. When unset, the proposer falls back to
    # `AZURE_OPENAI_DEPLOYMENT` so single-deployment setups still work.
    AZURE_OPENAI_SCHEMA_DEPLOYMENT: str | None = None
    AZURE_OPENAI_API_KEY: str | None = None
    AZURE_OPENAI_API_VERSION: str = "2024-10-21"

    def get_secret(self, env_name: str) -> str | None:
        """Read a secret value through Settings rather than raw os.environ.

        Verifier providers (and any other code paths that need to
        consume an operator-managed secret keyed by env-var name)
        should funnel through here. Two reasons:

          1. CLAUDE.md §6 forbids raw `os.environ.get(...)` outside
             `core/config.py`. Concentrating reads here gives us one
             place to add scrubbing / audit / `.env` normalisation in
             the future without chasing call sites.
          2. The pydantic-settings model picks up declared fields
             automatically (e.g. `ANTHROPIC_API_KEY`) — a bare
             `os.environ.get()` would miss `.env` loading on those.
             We try the model attribute first, then fall back to
             `os.environ` for fields the operator added themselves
             via VerifierConfig.api_key_env without us pre-declaring
             a Settings field for them.

        Returns None when the variable is unset, so callers can raise
        their own typed errors (e.g. VerifierAPIKeyMissingError).
        """
        import os as _os

        # Match attribute name case-insensitively against declared
        # Settings fields (CLAUDE.md leans on the pydantic-settings
        # `case_sensitive=False` config we already set).
        attr = getattr(self, env_name.upper(), None)
        if isinstance(attr, str) and attr:
            return attr
        return _os.environ.get(env_name) or None

    # ── Payload ──────────────────────────────────────────────────────
    PAYLOAD_KEY: str | None = None  # AES-256-GCM encryption key
    MAX_PAYLOAD_SIZE_MB: int = 5

    # NOTE: We deliberately do NOT carry a `WEBHOOK_SECRET` setting any
    # more. An earlier version had `WEBHOOK_SECRET: str =
    # "awaithumans-dev-secret"` — unused but a footgun: as soon as any
    # outbound-webhook signing code referenced it, every deployment
    # that didn't override the env var would HMAC with a value
    # publicly visible in the GitHub repo. If outbound webhook signing
    # is reintroduced, declare the field as `str | None = None` and
    # fail-fast in `app.py` when the dependent code path runs without
    # an explicit operator-set value, mirroring the PAYLOAD_KEY guard.

    # ── Dashboard embedding ──────────────────────────────────────────
    # See docs/superpowers/specs/2026-05-06-dashboard-embedding-design.md
    # §5.4 for the operational story; §4.2 for the JWT shape these
    # secrets sign. With env_prefix="AWAITHUMANS_" applied below, these
    # fields read from AWAITHUMANS_EMBED_SIGNING_SECRET,
    # AWAITHUMANS_EMBED_PARENT_ORIGINS, and AWAITHUMANS_SERVICE_API_KEY
    # respectively.
    EMBED_SIGNING_SECRET: str | None = None
    EMBED_PARENT_ORIGINS: str = ""
    SERVICE_API_KEY: str | None = None

    # ─── Demo (AwaitVerify landing demo) ─────────────────────────────
    TURNSTILE_SECRET: str | None = None
    DEMO_REVIEWER_EMAIL: str | None = None
    DEMO_HOT_SLACK_CHANNEL_ID: str | None = None
    # Slack mention prepended to hot-lane demo notifications. Default
    # `<!channel>` pings everyone in the founder channel — impossible
    # to miss on phone/computer. Override with `<@U123ABC>` for a
    # direct ping or set to an empty string to suppress entirely
    # (graceful fallback, no exception).
    DEMO_HOT_SLACK_MENTION: str = "<!channel>"
    DEMO_BOOKING_URL: str = "https://cal.com/awaithumans/15min"
    DEMO_PROVIDER: str = "openai-gpt-5"
    DEMO_CONFIDENCE_THRESHOLD: float = 0.85
    DEMO_CLAIM_TIMEOUT_SECONDS: int = 300  # public lane only; hot lane never times out
    DEMO_DAILY_CAP: int = 50
    DEMO_PER_IP_CAP: int = 3
    DEMO_PER_IP_WINDOW_HOURS: int = 24
    DEMO_PER_EMAIL_WINDOW_DAYS: int = 7
    DEMO_DAILY_COST_CEILING_CENTS: int = 500
    DEMO_FALLBACK_AFTER_HOURS: int = 72
    DEMO_EMAIL_ALLOWLIST_EXTRA: str = ""  # comma-separated extra domains

    model_config = {
        "env_prefix": "AWAITHUMANS_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        # Two namespaces share the `AWAITHUMANS_` prefix: server-side
        # (this Settings class) and SDK-side (`AWAITHUMANS_URL`,
        # `AWAITHUMANS_ADMIN_API_TOKEN` when used purely as a client
        # bearer, etc.). Without `extra="ignore"`, pydantic-settings'
        # dotenv source treats any unknown prefixed key as a
        # validation error and crashes Settings() at boot — so a
        # shared `.env` with an SDK-only key like `AWAITHUMANS_URL`
        # would kill `awaithumans dev`. We ignore unknown keys here
        # and emit a startup WARNING via `unknown_env_keys()` so the
        # operator still gets a heads-up if a key is actually a typo.
        "extra": "ignore",
    }

    @property
    def database_url_async(self) -> str:
        """Resolved async database URL.

        Accepts a libpq-style ``?sslmode=...`` query param (the form
        every managed Postgres provider — Azure, Heroku, Supabase,
        Render — hands you) and translates it to asyncpg's
        ``?ssl=...`` so the URL works without manual editing. asyncpg
        accepts the SAME set of values as libpq (``disable``, ``allow``,
        ``prefer``, ``require``, ``verify-ca``, ``verify-full``) under
        a different parameter name; without the translation the
        connection silently hangs or raises ``ClientConfigurationError``.
        """
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+asyncpg://", 1)
            elif url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return _translate_libpq_to_asyncpg(url)

        Path(self.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.DB_PATH}"

    @property
    def database_url_sync(self) -> str:
        """Resolved sync database URL (for migrations).

        Reverse of the async path: if the operator supplied an
        asyncpg-style ``?ssl=...`` param, translate it to
        ``?sslmode=...`` so psycopg (which alembic uses for migrations)
        accepts it. Most operators paste a libpq URL — but the
        async->sync round-trip should also work without surprises.
        """
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            return _translate_asyncpg_to_libpq(url)

        Path(self.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.DB_PATH}"

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse CORS_ORIGINS into a list."""
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.upper() == "PRODUCTION"


# Both libpq (sslmode=...) and asyncpg (ssl=...) accept the same
# set of values, just under different parameter names. The two
# helpers below translate between the styles so an operator's
# pasted-from-cloud-provider URL works on both the async runtime
# engine AND the sync alembic engine without manual editing.
_SSL_VALUES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")


def _translate_libpq_to_asyncpg(url: str) -> str:
    """``?sslmode=require`` → ``?ssl=require`` for every supported value.

    asyncpg's own DSN parser rejects ``sslmode`` as a parameter name
    (it understands the values but not the libpq key). Translating
    here means a single canonical ``DATABASE_URL`` env var works on
    both Postgres clients.
    """
    for value in _SSL_VALUES:
        url = url.replace(f"sslmode={value}", f"ssl={value}")
    return url


def _translate_asyncpg_to_libpq(url: str) -> str:
    """``?ssl=require`` → ``?sslmode=require``.

    Reverse of the above so psycopg (used by alembic for migrations)
    accepts an asyncpg-style URL too.
    """
    for value in _SSL_VALUES:
        url = url.replace(f"ssl={value}", f"sslmode={value}")
    return url


settings = Settings()


def unknown_env_keys(env_path: Path | None = None) -> list[str]:
    """Return AWAITHUMANS_* keys present in `.env` that this Settings ignores.

    Pydantic-settings (with our `extra="ignore"`) silently drops keys it
    doesn't recognize. That's the right default — keeps a shared `.env`
    with SDK vars like `AWAITHUMANS_URL` from crashing the server — but
    silent ignore can hide typos. This helper scans the `.env` file and
    returns every `AWAITHUMANS_*` key that didn't map to a declared
    server field, so the caller can WARN at boot.

    Returns `[]` when the env file is absent or has no unknown keys.
    """
    if env_path is None:
        # Mirror the Settings model_config value. Hardcoded to `.env`
        # because pydantic types `env_file` broadly enough (Path |
        # Sequence[Path | str]) that the .get() chain isn't worth
        # narrowing — we only ever set a single relative-cwd path.
        env_path = Path(".env")

    if not env_path.is_file():
        return []

    known = {f"AWAITHUMANS_{name.upper()}" for name in Settings.model_fields}
    unknown: list[str] = []
    try:
        contents = env_path.read_text(encoding="utf-8")
    except OSError:
        return []

    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key = line.split("=", 1)[0].strip().upper()
        if key.startswith("AWAITHUMANS_") and key not in known:
            unknown.append(key)
    return unknown
