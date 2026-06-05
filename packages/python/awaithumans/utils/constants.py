"""Project-wide constants.

All magic numbers, configuration defaults, and shared values live here.
Import from here, not from individual modules.
"""

from __future__ import annotations

from awaithumans.types import TaskStatus

# ─── Project Identity ────────────────────────────────────────────────────

SERVICE_NAME = "awaithumans"
DOCS_BASE_URL = "https://docs.awaithumans.dev"
DOCS_TROUBLESHOOTING_URL = f"{DOCS_BASE_URL}/troubleshooting"
DOCS_ROADMAP_URL = f"{DOCS_BASE_URL}/community/roadmap"

# ─── Timeout ─────────────────────────────────────────────────────────────

MIN_TIMEOUT_SECONDS = 60  # 1 minute — minimum allowed timeout
MAX_TIMEOUT_SECONDS = 2_592_000  # 30 days — maximum allowed timeout

# ─── Long-Poll ───────────────────────────────────────────────────────────

POLL_INTERVAL_SECONDS = 25  # reconnection interval, must stay under gateway timeouts (60s)

# ─── Timeout Scheduler ───────────────────────────────────────────────────

TIMEOUT_CHECK_INTERVAL_SECONDS = 5  # how often the scheduler checks for expired tasks

# ─── Task Status Sets ────────────────────────────────────────────────────

TERMINAL_STATUSES_SET = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.TIMED_OUT,
        TaskStatus.CANCELLED,
        TaskStatus.VERIFICATION_EXHAUSTED,
    }
)

# ─── Payload ─────────────────────────────────────────────────────────────

MAX_PAYLOAD_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB hard limit

# ─── Port Discovery ──────────────────────────────────────────────────────

# Where the server writes its port + URL so SDKs and the dashboard can auto-discover it.
# Located in the user's home directory so it's stable across cwd changes.
DISCOVERY_FILE_NAME = ".awaithumans-dev.json"

# ─── Slack Channel ───────────────────────────────────────────────────────

# Slack rejects requests whose timestamp is more than this many seconds old
# (prevents replay attacks).
SLACK_SIGNATURE_MAX_AGE_SECONDS = 300  # 5 minutes

# Block Kit action_id for the "Review" button in the initial notification.
SLACK_ACTION_OPEN_REVIEW = "awaithumans.open_review"

# Block Kit action_id for the "Claim" button on broadcast messages posted
# to a channel (notify=["slack:#ops"]). First clicker gets the task.
SLACK_ACTION_CLAIM_TASK = "awaithumans.claim_task"

# Block Kit callback_id for the review modal (matches on view_submission).
SLACK_MODAL_CALLBACK_ID = "awaithumans.review_modal"

# Prefix on Block Kit block_ids so we can recognize our own blocks and
# extract the field name deterministically.
SLACK_BLOCK_ID_PREFIX = "awaithumans:"

# notify= string prefix for Slack destinations. Examples:
#   "slack:#approvals"  → channel
#   "slack:@U123ABC"    → user (Slack user ID, starts with U or W)
SLACK_NOTIFY_PREFIX = "slack:"

# OAuth state is a signed, time-bounded nonce. Expires if the user takes
# more than this long to complete the install consent.
SLACK_OAUTH_STATE_MAX_AGE_SECONDS = 600  # 10 minutes

# Cap on Slack team_id strings used as path params (DELETE / list-members).
# Real Slack IDs top out around 11 chars (T123ABC456); 50 leaves headroom
# for enterprise prefixes without inviting an unbounded path scan.
SLACK_TEAM_ID_MAX_LENGTH = 50

# Maximum length we'll echo back as `?slack_oauth_error=...` on the
# redirect URL when something goes wrong during install. Long enough
# for a clear error code, short enough that nobody can stuff a 4KB
# query string into the redirect target.
SLACK_OAUTH_ERROR_PARAM_MAX_LENGTH = 100

# Timeout for the HTTPS exchange against Slack's OAuth `oauth.v2.access`
# endpoint. Slack itself responds within ~1s in practice; 10s gives
# headroom for a slow operator network without making the install
# UX feel hung.
SLACK_OAUTH_HTTP_TIMEOUT_SECONDS = 10

# Timeout for ephemeral-reply POSTs against the per-interaction
# `response_url`. These are best-effort UX courtesies (the modal /
# completion already happened); 5s keeps a flaky network from
# blocking the route's main path.
SLACK_RESPONSE_URL_TIMEOUT_SECONDS = 5

# Default timeout for the SDK's `POST /api/tasks` create call. Long
# enough to absorb a slow first-request DB warmup; short enough that
# misconfigured `server_url` fails fast with a clear unreachable
# error rather than hanging.
SDK_CREATE_TIMEOUT_SECONDS = 30

# Long-poll request timeout for the SDK. The server holds the
# connection up to POLL_INTERVAL_SECONDS; the client adds a small
# buffer so the underlying httpx call doesn't fire its own timeout
# right at the boundary.
SDK_POLL_TIMEOUT_BUFFER_SECONDS = 10

# Bot scopes we request during OAuth install. Kept in sync with
# channels/slack/app_manifest.yaml. Used as the default value for
# SLACK_OAUTH_SCOPES when the env var isn't overridden.
SLACK_DEFAULT_OAUTH_SCOPES = (
    "chat:write,im:write,channels:read,groups:read,users:read,files:write,files:read"
)

# Name of the cookie holding the OAuth state nonce — read from both
# /oauth/start (set) and /oauth/callback (verify + delete).
SLACK_OAUTH_STATE_COOKIE_NAME = "awaithumans_slack_oauth_state"

# Block Kit hard limits — text length caps and select-option counts.
# These are enforced by the Slack API itself; we truncate to stay well
# inside and fail loudly rather than have Slack silently drop blocks.
SLACK_HEADER_TEXT_MAX = 150
SLACK_PLAIN_TEXT_MAX = 3000
SLACK_SELECT_MAX_OPTIONS = 100
SLACK_CONTEXT_VALUE_MAX = 200  # payload-context key:value truncation in the modal header

# ─── Email Channel ───────────────────────────────────────────────────────

# Default TTL for magic-link action tokens. 24 hours covers "user comes
# back tomorrow morning"; 1 hour was too tight in practice.
MAGIC_LINK_MAX_AGE_SECONDS = 24 * 60 * 60

# HKDF parameters for deriving the magic-link HMAC key from PAYLOAD_KEY.
# The salt is channel-scoped so the same root key could derive keys for
# other channels without primitives colliding. `info` is a version tag —
# bump it on any breaking change to the token format.
MAGIC_LINK_HKDF_SALT = b"awaithumans-email-magic-links"
MAGIC_LINK_HKDF_INFO = b"v1"

# HMAC-SHA256 digest length. Used to slice mac || body when verifying
# magic-link tokens. Named so the slice isn't a magic number.
HMAC_SHA256_DIGEST_BYTES = 32

# Identity IDs are used in URLs (DELETE /identities/{id}) and in the
# `notify=` routing prefix (`email+acme:user@…`). 100 is more than enough
# for any human-chosen slug and short enough to keep URLs sane.
EMAIL_IDENTITY_ID_MAX_LENGTH = 100

# ─── Verifier ────────────────────────────────────────────────────────────

# Default model + API-key env var per provider. Operators can override
# either via VerifierConfig (the per-task config carries `model` and
# `api_key_env` fields) — these are the fall-throughs. Bumping a default
# model here changes the behaviour of any task that didn't pin one.

VERIFIER_CLAUDE_DEFAULT_MODEL = "claude-sonnet-4-5"
VERIFIER_CLAUDE_DEFAULT_API_KEY_ENV = "ANTHROPIC_API_KEY"
# Anthropic forces structured output via tool-use; this is the tool name
# we register and force-select. Stable string — Slack/email/dashboard
# don't see it, but tests assert against it.
VERIFIER_CLAUDE_TOOL_NAME = "submit_verdict"

VERIFIER_OPENAI_DEFAULT_MODEL = "gpt-4o-2024-11-20"
VERIFIER_OPENAI_DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"

VERIFIER_GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"
VERIFIER_GEMINI_DEFAULT_API_KEY_ENV = "GEMINI_API_KEY"

VERIFIER_AZURE_DEFAULT_API_KEY_ENV = "AZURE_OPENAI_API_KEY"
VERIFIER_AZURE_DEFAULT_ENDPOINT_ENV = "AZURE_OPENAI_ENDPOINT"
VERIFIER_AZURE_DEFAULT_API_VERSION = "2024-10-21"

# Per-call token cap. Verifier responses are short structured JSON; this
# is sized to fit `passed` + `reason` + `parsed_response` for any
# realistic schema, with headroom for a multi-sentence reason.
VERIFIER_MAX_OUTPUT_TOKENS = 1024

# JSON-schema name passed to OpenAI / Azure structured-output. Slack and
# the dashboard never see it, but it shows up in vendor logs and the
# error-debug surface so a stable, descriptive name helps.
VERIFIER_OUTPUT_SCHEMA_NAME = "verifier_verdict"

# ─── Outbound webhooks (callback_url) ────────────────────────────────────

# HKDF parameters for deriving the webhook-signing HMAC key from
# PAYLOAD_KEY. Channel-scoped salt mirrors the session + magic-link
# pattern so the same root key never signs two distinct primitives.
# Bumping `info` is a versioned breaking change — old signatures
# stop verifying, callers must migrate.
WEBHOOK_HKDF_SALT = b"awaithumans-webhook-v1"
WEBHOOK_HKDF_INFO = b"v1"

# Header name carrying the body's HMAC signature. Format is
# `sha256=<hex>` so receivers can future-proof for an algorithm
# upgrade by inspecting the prefix.
WEBHOOK_SIGNATURE_HEADER = "X-Awaithumans-Signature"

# Timeout for the outbound POST. The receiver's job is "verify HMAC,
# enqueue, return 200" — under 1s in practice. 10s gives headroom
# for a slow-starting Lambda; long enough that flaky receivers
# don't always fail, short enough not to stack BackgroundTasks
# during an outage.
WEBHOOK_DELIVERY_TIMEOUT_SECONDS = 10

# Backoff schedule for retried deliveries — index = attempt_count
# AFTER the failed attempt. Cumulative wall-clock at the LAST entry
# stays under 3 days so we always exhaust the schedule before the
# hard cap kicks in. Tuning intent: aggressive in the first hour
# (most failures are transient receiver flaps), then hourly for the
# next workday, then daily so a multi-day outage still gets one
# attempt per day without burning through retries.
WEBHOOK_RETRY_BACKOFF_SECONDS: tuple[int, ...] = (
    30,  # 30s
    60,  # 1m
    120,  # 2m
    300,  # 5m
    900,  # 15m
    1800,  # 30m
    3600,  # 1h
    7200,  # 2h
    14400,  # 4h
    28800,  # 8h
    86400,  # 24h
    86400,  # 24h
    86400,  # 24h
)

# Hard cap on how long a delivery row stays in the queue. Past this
# (measured from the row's `created_at`), the dispatcher marks the
# row ABANDONED and stops attempting. 3 days = "if your receiver
# has been down this long, the workflow has almost certainly already
# timed out anyway, and a human should redrive manually rather than
# us silently flapping forever."
WEBHOOK_RETRY_MAX_AGE_SECONDS = 3 * 24 * 3600

# How often the webhook scheduler wakes to claim due rows. Smaller =
# lower delivery latency; larger = less DB load. 5s mirrors the
# timeout scheduler — cheap because the "are any rows due?" query
# uses the indexed (status, next_attempt_at) tuple.
WEBHOOK_SCHEDULER_INTERVAL_SECONDS = 5

# ─── Dashboard Auth ──────────────────────────────────────────────────────

# Name of the dashboard session cookie. Set by POST /api/auth/login,
# cleared by POST /api/auth/logout, verified on every non-public route.
DASHBOARD_SESSION_COOKIE_NAME = "awaithumans_session"

# 7 days. Short enough that a stolen cookie has a bounded blast radius,
# long enough that humans don't re-login every day.
DASHBOARD_SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

# HKDF parameters for deriving the session-cookie HMAC key from
# PAYLOAD_KEY. Channel-scoped salt mirrors the magic-link pattern so
# the same root key never signs two different primitives.
DASHBOARD_SESSION_HKDF_SALT = b"awaithumans-dashboard-session"
DASHBOARD_SESSION_HKDF_INFO = b"v1"

# HKDF parameters for the Slack-handoff URL signature. A signed URL
# travels in the Slack DM that announces a task — clicking it mints a
# session cookie for the recipient. Slack-only users (no email/password)
# can't use the normal login form so this is the only way they reach
# the dashboard. URL is bound to (user_id, task_id, expiry); the expiry
# is set to the task's `timeout_at` so the link stays usable for the
# whole task lifetime, no matter how long that is.
SLACK_HANDOFF_HKDF_SALT = b"awaithumans-slack-handoff"
SLACK_HANDOFF_HKDF_INFO = b"v1"

# HKDF parameters for the Email-handoff URL signature. Mirror of the
# Slack-handoff but for the email channel — the link in a "Review in
# dashboard" email is signed so the recipient can clear the dashboard
# login wall even when their email isn't registered as a reviewer
# yet. The endpoint auto-provisions a passwordless directory user on
# first click (the agent's `notify=` is implicit consent to provision).
EMAIL_HANDOFF_HKDF_SALT = b"awaithumans-email-handoff"
EMAIL_HANDOFF_HKDF_INFO = b"v1"

# ─── Dashboard embedding ────────────────────────────────────────────────────
# See docs/superpowers/specs/2026-05-06-dashboard-embedding-design.md (§4.2, §5.3).

# Embed token TTL constants: default (5 min), minimum (1 min), maximum (1 hour).
# Used by the token-mint endpoint to set and validate expiration times.
EMBED_TOKEN_DEFAULT_TTL_SECONDS = 300
EMBED_TOKEN_MIN_TTL_SECONDS = 60
EMBED_TOKEN_MAX_TTL_SECONDS = 3600

# JWT claims for embed tokens. Audience and issuer are bound to the token's
# purpose (embedding) and origin (awaithumans service) to prevent token reuse.
EMBED_TOKEN_AUDIENCE = "embed"
EMBED_TOKEN_ISSUER = "awaithumans"

# Leeway for JWT verification (clock skew tolerance). Accounts for minor
# time drift between the token issuer and validator.
EMBED_TOKEN_LEEWAY_SECONDS = 60

# Service key format constants: prefix on the display string, raw key material
# length in bytes, how many chars of the prefix to show in logs/UIs,
# and the maximum display name length.
SERVICE_KEY_PREFIX = "ah_sk_"
SERVICE_KEY_RAW_BYTES = 20
SERVICE_KEY_DISPLAY_PREFIX_LENGTH = 12
SERVICE_KEY_MAX_NAME_LENGTH = 80

# ─── AwaitVerify (managed document verification) ─────────────────────────
# See `pillars/12-awaitverify.md` (rev 3) for product spec.

# Marker on `assign_to.managed` that routes a task to the AwaitVerify
# managed reviewer pool. The server's router recognizes this value and
# dispatches to the AwaitVerify queue rather than the customer's own
# user directory. Stable string — bump only for breaking server changes.
AWAITVERIFY_MANAGED_TAG = "awaitverify"

# Five-region client-side fragmentation: one base document → five masked
# views. Pillar 12 rev 3 §"Security architecture" pins this at five.
AWAITVERIFY_FRAGMENT_COUNT = 5

# Soft cap on document pages per verify_document call. With signed-URL
# uploads (v1) the payload-size ceiling no longer applies, so we can
# raise this from the original 10. 100 is a generous ceiling that
# still gives us a defensible "single document" bound.
AWAITVERIFY_MAX_PAGES = 100

# AwaitVerify timeout floor and default. We require a minimum of 24
# hours because our SLA guarantee for standard priority is ~3-4 hours
# and we want generous headroom before the task gets marked
# `timed_out`. Default is 48 hours so customers have a working day
# of buffer before they need to think about extending. Customers who
# need to bail out of their local process earlier can wrap the call
# in `asyncio.wait_for(...)`; the task continues server-side either
# way.
AWAITVERIFY_MIN_TIMEOUT_SECONDS = 24 * 60 * 60  # 24 hours
AWAITVERIFY_DEFAULT_TIMEOUT_SECONDS = 48 * 60 * 60  # 48 hours

# pdf2image rasterization DPI. 300 is print quality, sufficient for
# handwritten and small-font content (T12C3 vs 712C3 readability test).
# Higher DPI inflates fragment size; lower DPI risks misreads.
AWAITVERIFY_PDF_RASTER_DPI = 300

# Pillow image formats accepted as input. PDF is handled separately
# by pdf2image. Office formats are converted to PDF first via
# LibreOffice headless (system binary).
AWAITVERIFY_SUPPORTED_IMAGE_FORMATS = frozenset({"PNG", "JPEG", "TIFF", "BMP", "WEBP", "GIF"})

# Office formats Pillow / pdf2image cannot decode but LibreOffice can
# convert. Detection by file-extension hint plus magic-byte sniffing
# (ZIP for modern Office Open XML, OLE CFB for legacy formats).
AWAITVERIFY_SUPPORTED_OFFICE_EXTENSIONS = frozenset(
    {".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".odt", ".ods", ".odp", ".rtf"}
)

# LibreOffice binary name. Override with the AWAITHUMANS_LIBREOFFICE_BIN
# env var if the binary lives at a non-standard location.
AWAITVERIFY_LIBREOFFICE_BIN_ENV = "AWAITHUMANS_LIBREOFFICE_BIN"
AWAITVERIFY_LIBREOFFICE_DEFAULT_BIN = "libreoffice"

# Max wall time for the LibreOffice conversion subprocess. Office
# files of typical size convert in 1-5s; 60s gives generous headroom.
AWAITVERIFY_LIBREOFFICE_TIMEOUT_SECONDS = 60
