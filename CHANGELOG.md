# Changelog

All notable changes to `awaithumans` are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Dates are ISO-8601. Unreleased changes land in the top section and roll
into a versioned release when tagged.

---

## [Unreleased]

### Added

- **`AwaitHumans` client class.** Initialize once with your API key and
  reuse across calls instead of passing credentials per call:

  ```python
  from awaithumans import AwaitHumans
  client = AwaitHumans(
      api_key="ah_sk_live_...",
      providers={"openai": "sk-..."},  # for Flow B
  )
  result = await client.verify_document(...)
  ```

  Module-level shim functions (`verify_document`, `await_human`) still
  work for one-off scripts; they lazily create a default client from
  environment variables. Provider credentials configured on the client
  are used locally and never transmitted to AwaitVerify infrastructure.

- **AwaitVerify managed document verification (Python SDK).** New
  `verify_document` function (alias: `awaitVerify`) supporting all three
  flows:

  - **Flow A — Human Only:** pass `prior_extraction=...` (your already-
    extracted data, which the human verifies)
  - **Flow B — Model Then Human:** pass `extraction=ExtractionConfig(...)`
    with provider + model + prompt. The SDK runs the model on the
    caller's machine using customer-provided credentials, gets structured
    output, and routes that output plus fragments to the human. v1 ships
    OpenAI; other providers follow.
  - **Flow C — Human Then Model:** pass `verifier=...` to enable the
    existing AI verifier loop on the human's submission.

  Documents are loaded locally and fragmented into five masked PNG views
  client-side (Pillow + pdf2image, behind the new `[awaitverify]` extra).
  The full unfragmented document never leaves the customer's environment.
  Supports PDF, PNG, JPEG, TIFF, BMP, WEBP, GIF inputs. Up to **100 pages
  per call**. Per-task `priority="standard"` or `"high"` selects the SLA
  queue (Express = 2× rate, 30-min target firm Mon-Fri 8am-8pm ET).
  See `pillars/12-awaitverify.md` rev 3.

- **Dual-style function aliases.** Existing `await_human` /
  `await_human_sync` now have camelCase aliases `awaitHuman` /
  `awaitHumanSync` exported from the top-level `awaithumans` namespace.
  Same function objects, second name. The new `verify_document` is also
  aliased as `awaitVerify`. Class methods on `AwaitHumans` expose both
  styles too (`client.await_human()` and `client.awaitHuman()`).

- **Internal task timeout for AwaitVerify is hardcoded** at 24 hours.
  Callers do not pass `timeout_seconds` on `verify_document`. This
  prevents customer-set timeouts from firing before our SLA can be met
  (standard SLA minimum is ~3-4 hours). Customers who want to abandon
  an awaitable sooner can wrap the call in `asyncio.wait_for(...)`.

- **PDF rasterization at 300 DPI ("print quality")** so small handwritten
  content survives the rasterization step. PDFs were previously
  rasterized at pdf2image's default DPI (200).

- **`[awaitverify]` install extra** installs Pillow + pdf2image for
  client-side fragmentation. Calling `verify_document()` without it
  raises `VerifyDepsMissingError` with a clear install hint.

- **`[awaitverify-openai]` install extra** installs `openai` for Flow B.
  Calling `verify_document(extraction=...)` with `provider="openai"`
  without this extra raises `VerifyDepsMissingError`.

### Refactored (still pre-release — rev 4)

- **Typed provider configuration.** Removed the `providers={"openai":
  "sk-..."}` dict-keyed API. Replaced with explicit named constructor
  kwargs on `AwaitHumans`:

  ```python
  from awaithumans import AwaitHumans
  from awaithumans.providers import OpenAI, Anthropic

  client = AwaitHumans(
      api_key="ah_sk_...",
      openai=OpenAI(api_key="sk-..."),
      anthropic=Anthropic(api_key="sk-ant-..."),  # reserved, v1.1
  )
  ```

  Adding a provider requires a typed config class, not a magic string
  key. Typos are caught by the type checker.

- **`ExtractionConfig` is now a typed union of provider-specific
  classes.** Per-call Flow B uses `OpenAIExtraction(model="gpt-5",
  prompt="...")` instead of a generic `ExtractionConfig(provider=
  "openai", ...)`. Same pattern as the credentials.

- **`prior_extraction` is now Pydantic-only.** `dict[str, Any]` is no
  longer accepted. Callers must pass a `BaseModel` instance. The SDK
  serializes to JSON internally.

- **`document_path` accepts URLs as well as local paths.** The SDK
  detects `http://` / `https://` prefix and fetches locally. Removed
  the separate `document_url` parameter. `document_bytes` stays
  separate for raw-bytes callers.

- **`timeout_seconds` is back, with a 24-hour minimum.** Default 24
  hours. Maximum 30 days. Setting below 24 hours raises
  `VerifyTimeoutRangeError` with a clear hint pointing at
  `asyncio.wait_for` for client-side early-exit. The 24-hour floor
  protects against races with our 3-4 hour standard SLA.

- **Office document support.** Adds DOCX, XLSX, PPTX, DOC, XLS, PPT,
  ODT, ODS, ODP, RTF via LibreOffice headless conversion. Requires the
  `libreoffice` system binary on PATH (override with
  `AWAITHUMANS_LIBREOFFICE_BIN`). Detection via ZIP / OLE / RTF magic
  bytes; conversion happens in a temporary directory wiped on exit.
  Combined supported-formats list now: **PDF, PNG, JPEG, TIFF, BMP,
  WEBP, GIF, DOCX, XLSX, PPTX, DOC, XLS, PPT, ODT, ODS, ODP, RTF.**

- **Page cap raised from 10 to 100.** With signed-URL transport
  arriving in the backend Phase 1, the original 5 MB payload ceiling
  stops being the constraint.

- **PDF rasterization at 300 DPI explicitly** (was the pdf2image
  default of 200). Preserves small handwritten content (T12C3 vs
  712C3 readability).

---

## [0.1.7] — 2026-05-24

Two-fix release, both surfaced by real first-time-user debugging on a fresh machine. No API surface changes — safe drop-in upgrade.

### Fixed

- **Docker image now ships all verifier extras.** v0.1.6's published image only included `[server]`, so the moment a user sent a task with `VerifierConfig(provider="claude" | "openai" | "gemini" | "azure_openai")` to the official image, the server failed on response submission with *"Verifier provider 'X' requires the [verifier-X] extra. Install with: pip install awaithumans[verifier-X]"* — which the operator couldn't act on because the server lived inside an image. The `await_human()` call never resolved; the agent hung until timeout. Image now installs all four verifier extras (~35 MB added). Closes [#142](https://github.com/awaithumans/awaithumans/issues/142). ([#143](https://github.com/awaithumans/awaithumans/pull/143))
- **Slack interactivity auto-links a Slack identity to your operator account on first click.** Previously, clicking `Open in Slack` or `Claim` on a task message refused with *"You're not in this server's user directory. Ask your operator to add you via Settings → Users."* — even when the clicker WAS the operator who installed the Slack app and signed up via `/setup`. Recovery required digging up two Slack IDs and pasting them into the dashboard's user-edit form. Now the server calls Slack's `users.info` API for the clicker's email and atomically binds the Slack identity to a matching directory user. Requires the Slack app's bot token to have the `users:read.email` scope. Closes [#144](https://github.com/awaithumans/awaithumans/issues/144). ([#145](https://github.com/awaithumans/awaithumans/pull/145))

### Maintenance

- Python and TypeScript stay mono-versioned at `0.1.7`.
- Docker image republished as `ghcr.io/awaithumans/awaithumans:v0.1.7` (also retagged `:latest`).

---

## [0.1.6] — 2026-05-23

Three-fix release, all surfaced by real first-run-onboarding debugging on a fresh machine. No API changes — safe drop-in upgrade.

### Fixed

- **PyPI README's demo GIF is no longer a broken image.** v0.1.5's `hero-demo.gif` was 10.5 MB at 1402×720 / 33fps. PyPI's image proxy (Camo) rejects responses over ~5 MB, so the README rendered with a broken-image icon for every visitor. Re-encoded with ffmpeg's two-pass palette to 720px / 10fps / 64 colors → **3.24 MB** (69% smaller) while still showing the full demo flow. Render path: `cdn.jsdelivr.net/gh/awaithumans/awaithumans@v0.1.6/docs/images/hero-demo.gif`. ([#138](https://github.com/awaithumans/awaithumans/pull/138))
- **First-run setup form: every input now has placeholder text.** Empty `<input>` boxes with just a small label gave new operators zero hint about the expected format. Watched a real user type an arbitrary placeholder-shaped string into the email field on `/setup`, succeed, then be unable to log in. Placeholders added on `/setup` (token, email, display name, password, confirm) and `/login` (email, password). Closes [#136](https://github.com/awaithumans/awaithumans/issues/136). ([#138](https://github.com/awaithumans/awaithumans/pull/138))
- **Post-signup OnboardingPanel now echoes the email + display name you registered.** The "Operator created" screen previously jumped straight to SDK code examples without confirming what got persisted, so users had no way to recover the credentials they just typed. Adds a small brand-tinted "Signed in as" card above the code. Closes [#137](https://github.com/awaithumans/awaithumans/issues/137). ([#138](https://github.com/awaithumans/awaithumans/pull/138))

### Maintenance

- Python and TypeScript stay mono-versioned at `0.1.6`.
- Docker image republished as `ghcr.io/awaithumans/awaithumans:v0.1.6` (also retagged `:latest`).

---

## [0.1.5] — 2026-05-19

Two days after [v0.1.4](https://github.com/awaithumans/awaithumans/releases/tag/v0.1.4). Maintenance + discovery release: one PyPI-rendering bug fix from beta-tester feedback, the test-CI infrastructure that was missing, follow-up cleanups CI surfaced on first run, and a deliberate npm-keyword expansion as a discovery experiment. No API changes; safe drop-in upgrade.

### Fixed

- **PyPI README images render again.** v0.1.4's README pointed at `raw.githubusercontent.com`, which serves `application/octet-stream` — PyPI's strict MIME check rejects that and just doesn't render the image. Switched the demo GIF + logo to `cdn.jsdelivr.net/gh/`, which proxies GitHub content with the correct `image/gif` / `image/png` headers. npm rendered fine on 0.1.4; this fix is PyPI-specific. ([#124](https://github.com/awaithumans/awaithumans/pull/124))
- **Python 3.10 support is real again.** Two production modules (`server/routes/embed.py`, `server/services/service_key_service.py`) plus one test file imported `datetime.UTC`, which only exists in 3.11+. pyproject declared 3.10 as the floor but the package crashed on import. Swapped to the long-form `datetime.timezone.utc`. ([#126](https://github.com/awaithumans/awaithumans/pull/126))
- **Temporal adapter test collection no longer breaks the suite.** `tests/adapters/test_temporal_adapter.py` still imported `_create_task_activity`, the pre-refactor name; the public symbol is now `awaithumans_create_task`. Updated four call sites. ([#127](https://github.com/awaithumans/awaithumans/pull/127))
- **Dashboard had four real unused locals** (`cn` import + `DEFAULT_FILTERS` const, in both the task queue and audit pages) — surfaced once strict tsconfig flags went in. ([#129](https://github.com/awaithumans/awaithumans/pull/129))

### Added

- **GitHub Actions tests workflow.** Three parallel jobs run on every PR and every push to `main`: Python 3.11 + 3.13 matrix (pytest), TypeScript SDK (vitest + tsc), dashboard (vitest + tsc). Concurrency group cancels superseded runs. The existing `migrations.yml` continues to cover Alembic separately. ([#125](https://github.com/awaithumans/awaithumans/pull/125))
- **`noUnusedLocals` + `noUnusedParameters` in `tsconfig.base.json`** — future regressions now fail `npm run typecheck` instead of slipping through. ([#129](https://github.com/awaithumans/awaithumans/pull/129))

### Changed

- **Ruff cleanup pass across the Python package.** Lint drift had accumulated to 165 findings (mostly import-sort + trailing-comma); `ruff check --fix` + `ruff format` resolved 154 of them, 14 manual edits cleaned up the rest. `ruff check .` is now green; CI re-enabling the lint step is queued as a follow-up. No behavior change. ([#128](https://github.com/awaithumans/awaithumans/pull/128))
- **TypeScript SDK npm keywords: 33 → 224** as a deliberate discovery experiment. npm is treated as a discovery surface rather than a one-liner index — the README, not the keyword list, judges fit. No code change; metadata only. ([#130](https://github.com/awaithumans/awaithumans/pull/130))

---

## [0.1.4] — 2026-05-17

Eight PRs of bug fixes and DX improvements caught by beta-tester
feedback over the 0.1.3 → 0.1.4 window, plus the full marketing
README refresh.

### Added

- **`GET /api/version`** — public endpoint returning `{"name":
  "awaithumans", "version": "..."}`. Useful for ops monitoring,
  pre-auth SDK compatibility probes, and reverse-proxy
  misconfig debugging. Auth-bypass entry added so it works without
  a session. ([#117](https://github.com/awaithumans/awaithumans/pull/117))
- **`Idempotent-Replayed: true` response header** on
  `POST /api/tasks` when the call returns an existing task via the
  idempotency key. Status stays `201` (matches Stripe's
  contract — flipping to `200` would break clients that check
  the specific code). Documented in `docs/api/overview.mdx`.
  ([#118](https://github.com/awaithumans/awaithumans/pull/118))
- **`notification_failed` audit entries + banner** on the task
  detail page when an email or Slack send couldn't deliver. The
  previous silent-drop behaviour left operators wondering why
  a human never got pinged. Email surfaces all four failure
  modes (no transport configured, no From: address, transport
  error, internal error); Slack surfaces three (no client, target
  not found, post-message error). ([#111](https://github.com/awaithumans/awaithumans/pull/111))
- **Startup channel-config validator** warns at boot if a channel
  is half-configured (e.g. `EMAIL_TRANSPORT=smtp` set but
  `SMTP_HOST` missing). Catches the misconfig before the first
  send silently fails. ([#112](https://github.com/awaithumans/awaithumans/pull/112))
- **Brand-styled HTML page** on email- / Slack-handoff link
  failure. Recipients clicking a stale link in a browser used to
  see raw FastAPI JSON; now they get a friendly card on the same
  dark surface as the existing confirmation / completed pages.
  ([#116](https://github.com/awaithumans/awaithumans/pull/116))

### Fixed

- **Email-handoff URL no longer expires instantly for east-of-UTC
  users.** SQLite stores `task.timeout_at` tz-naive; the email
  notifier was calling `.timestamp()` on it which treats the
  value as local time. For UTC+1 users a fresh 10-minute task
  issued a link born 3,000 seconds expired. Fix extracted to a
  shared `awaithumans.utils.time.to_utc_unix` helper used by both
  the email and Slack handoff paths. ([#113](https://github.com/awaithumans/awaithumans/pull/113))
- **Duplicate notifications on idempotent retries.** The
  `POST /api/tasks` route was firing `notify_task_*` background
  tasks unconditionally; a retry with the same idempotency key
  re-emailed / re-Slacked the reviewer for already-in-flight
  work. `create_task` now returns `(task, was_newly_created)` and
  the route gates notify on the flag. ([#114](https://github.com/awaithumans/awaithumans/pull/114))
- **OpenAPI docs now at `/api/docs`** to match the docs page
  contract (which had been promising that path while the actual
  routes lived at `/docs`). Auth-bypass updated; `version=` in
  the FastAPI constructor reads from `awaithumans.__version__`
  instead of the hardcoded `0.1.1`. ([#115](https://github.com/awaithumans/awaithumans/pull/115))
- **CopyButton on the dashboard** works in all contexts now —
  previously, a parent row's `onClick` could preempt the
  clipboard write, and the `navigator.clipboard` failure path
  was silent. Adds `stopPropagation`, a legacy
  `document.execCommand("copy")` fallback, and `console.warn`
  on hard failure. Plus copy buttons added to the audit-log list
  rows next to each task ID. ([#112](https://github.com/awaithumans/awaithumans/pull/112))
- **All docs URLs** point at the real subdomain
  `docs.awaithumans.dev` instead of the dead `awaithumans.dev/docs`
  path. 13 files updated; verified each previously-broken URL
  returns 200. ([#113](https://github.com/awaithumans/awaithumans/pull/113))

### Docs / Marketing

- **Hero structure + brand logo + comparison table** on the
  GitHub README, plus matching hero blocks on the PyPI and npm
  package READMEs. New "Why awaithumans" comparison table
  positioned between the problem statement and the quick start
  — captures "humanlayer alternative" search traffic, the
  strongest single positioning lever. ([#119](https://github.com/awaithumans/awaithumans/pull/119)
  / [#120](https://github.com/awaithumans/awaithumans/pull/120))
- **Copy-pasteable Quick start** rewritten as a single bash
  heredoc block. New "What you can build with it" section with
  six concrete production patterns (high-value approvals, KYC,
  content moderation, agent-PR review, customer-success
  escalation, scrape-and-CAPTCHA fallback). ([#121](https://github.com/awaithumans/awaithumans/pull/121))
- **Adoption badges** (PyPI installs, npm installs, GitHub stars)
  promoted to a prominent for-the-badge row in the hero, above
  the small flat metadata row. PyPI install badge switched to
  pepy.tech to fix the "rate limited by upstream service" error
  that was showing in production. ([#122](https://github.com/awaithumans/awaithumans/pull/122))
- **Keyword expansion** on both PyPI (10 → 33) and npm (8 → 32),
  covering problem terms, framework names (LangChain / LangGraph /
  CrewAI / AutoGen / Pydantic AI / Temporal / MCP), model
  provider names (Claude / Anthropic / OpenAI / GPT / Gemini),
  use cases (KYC / content-moderation / agent-safety), and
  competitor-capture (`human-layer` for the abandoned humanlayer
  package).
- **GitHub repo topics** expanded to the full 20-slot maximum
  with the same strategic mix.

### Versions

- Python `awaithumans`: `0.1.3` → `0.1.4`
- TypeScript `awaithumans`: `0.1.3` → `0.1.4` (mono-version sync;
  the package source is unchanged this release — the SDK is a
  thin HTTP client and all the new server endpoints / behaviour
  ride on the existing wire protocol)

---

## [0.1.3] — 2026-05-14

### Fixed

- **Email-handoff URLs no longer expire instantly for East-of-UTC users.**
  SQLite + SQLModel stores `task.timeout_at` tz-naive;
  `int(task.timeout_at.timestamp())` was interpreting the naive value as
  local time, shifting the URL's `e` parameter by the local-UTC offset.
  For users east of UTC, a fresh 10-minute task was issued a link born
  already expired by the offset (e.g. UTC+1 → 50 minutes past expiry at
  creation time). Fix extracted to a shared
  `awaithumans.utils.time.to_utc_unix` helper used by both the email
  and Slack handoff paths. Regression tests run under
  `TZ=Africa/Lagos`. ([#107](https://github.com/awaithumans/awaithumans/pull/107))

- **Unknown `AWAITHUMANS_*` keys in `.env` are silently ignored**
  (with a one-shot startup `WARNING` listing them) instead of crashing
  `Settings()` on boot with a pydantic `extra_forbidden` error.
  The `AWAITHUMANS_` prefix is shared by the SDK (`AWAITHUMANS_URL`,
  etc.) and the server; pydantic-settings' dotenv source previously
  enforced `extra="forbid"` by default, killing the server whenever a
  shared `.env` carried any SDK-side key. Typos still surface via the
  warning. ([#108](https://github.com/awaithumans/awaithumans/pull/108))

- **CLI `awaithumans dev` error message rewritten** when the bare SDK
  is installed without the `[server]` extras. Now follows the
  what → why → fix → docs pattern with an actionable docs URL,
  instead of a one-line `SystemExit`. ([#106](https://github.com/awaithumans/awaithumans/pull/106))

### Docs

- **`docs/sdk/python.mdx` install section restructured** to lead with
  the two main install paths (run a server vs call a server) and
  explain how to stack extras like `[server,temporal,verifier-claude]`.
  ([#106](https://github.com/awaithumans/awaithumans/pull/106))
- **`docs/troubleshooting.mdx`** gains a new
  `### cli-missing-server-extra` section so the URL in the new CLI
  error message resolves to a real anchor.
  ([#106](https://github.com/awaithumans/awaithumans/pull/106))
- **`docs/self-hosting/configuration.mdx`** opens with a new
  "Two namespaces under one prefix" section documenting the
  SDK/server split and the silent-ignore + warning policy.
  ([#108](https://github.com/awaithumans/awaithumans/pull/108))

### Versions

- Python `awaithumans`: `0.1.2` → `0.1.3`
- TypeScript `awaithumans`: `0.1.2` → `0.1.3` (mono-version sync; no
  TypeScript SDK source changes this release)

---

## [0.1.2] — 2026-05-12

### Fixed

- **Bare `notify=["email:user@x"]` now routes through a single configured
  DB identity** when `AWAITHUMANS_EMAIL_TRANSPORT` is unset. Operators
  who set email up through the dashboard saw the docs quickstart example
  silently skip because nothing pointed at their identity — now it
  "just works" when exactly one identity is configured. Multi-identity
  deployments still require explicit `email+<id>:...` (no arbitrary
  pick). Existing env-configured deployments unchanged. ([#101](https://github.com/awaithumans/awaithumans/pull/101))

- **SMTP factory accepts `user` as an alias for `username`.** The
  dashboard form hint advertised `user`, Python's stdlib `smtplib`
  uses `user` too, but the factory only read `username` — credentials
  were silently dropped, producing opaque auth failures. Explicit
  `username` still wins when both keys are present. ([#101](https://github.com/awaithumans/awaithumans/pull/101))

- **SMTP factory defaults `use_tls=True` on port 465.** Port 465 is
  implicit-TLS by convention; the previous default of `use_tls=False,
  start_tls=True` attempted STARTTLS on an implicit-TLS port and failed
  the handshake — the exact trap operators hit with Hostinger, Zoho,
  Fastmail, and most managed SMTP providers. Explicit overrides are
  still respected. ([#101](https://github.com/awaithumans/awaithumans/pull/101))

- **Listing email identities tolerates rows encrypted under a rotated
  or stale `PAYLOAD_KEY`.** A single undecryptable row used to 500 the
  entire `GET /api/channels/email/identities` endpoint (the Settings
  page showed "An unexpected error occurred."). The list view now
  defers the encrypted `transport_config` column so a single bad row
  doesn't poison the response; per-row ops that actually need the
  secret still surface decryption failures loudly at use-time. ([#100](https://github.com/awaithumans/awaithumans/pull/100))

- **Dashboard SMTP form hint shows a port-465 example** and uses the
  canonical `username` key. The Email-sender-identities panel
  description now also mentions the bare-`email:` solo-identity
  shortcut so the UI matches the docs. ([#101](https://github.com/awaithumans/awaithumans/pull/101))

### Docs

- **`docs/channels/email.mdx`** documents the solo-identity shortcut
  in both "Two ways to configure" and "Route to a specific identity"
  so the quickstart example is honest for operators who configure
  through the dashboard.

### Version note

The **TypeScript SDK has no functional changes** — `awaithumans@0.1.2`
on npm is byte-equivalent to `0.1.1` at the source level. The bump is
purely to keep the Python and TypeScript SDK versions in lock-step;
pinning one and pinning the other to the same version remains the
recommended pattern.

---

## [0.1.1] — 2026-05-11

### Security

- **Bumped bundled Next.js `16.2.3` → `16.2.6`** in the dashboard,
  clearing 13 GHSA advisories. The dashboard ships statically built
  into the Python wheel, so this fix only reaches PyPI users via a
  republish — bump the Python version accordingly.

### Fixed

- **TypeScript SDK: widen `@langchain/langgraph` peer-dep range** to
  `"^0.2.0 || ^1.0.0"` (was `"^0.2.0"`). Users on a fresh
  `npm install awaithumans @langchain/langgraph` would get the
  current upstream (`1.x`) and hit `ERESOLVE` against the old
  pinned range. Verified the `interrupt(...)` API surface the
  adapter uses is signature-identical across both majors. No
  runtime code changed; this is purely a peer-range fix.

- **Python package version bumped 0.1.0 → 0.1.1** so the bundled-Next.js
  security fix above can be republished to PyPI. Mono-version with the
  TypeScript SDK at 0.1.1.

---

## [0.1.0] — 2026-05-11

First tagged release. Everything below is in the shipped package.

### Changed (BREAKING)

- **Idempotency keys follow strict Stripe semantics.** A task's
  `idempotency_key` always returns the same task, regardless of
  status. Previously a terminal task's key was released, allowing a
  fresh task with the same key — convenient for "re-trigger a
  review" but silently lost the human's response when an agent
  restart raced with task completion in direct mode. To request a
  fresh task for the same logical event, pass a distinct key (e.g.
  suffix with `:retry-1`). Direct-mode `await_human()` is now
  resumable across agent restarts: a re-invocation with the same
  key returns the stored response (for `COMPLETED` tasks) or the
  typed terminal error (for `TIMED_OUT` / `CANCELLED` /
  `VERIFICATION_EXHAUSTED`). Aligns the implementation with the
  Stripe model the docs already claimed.

- **Repo and packages are now Apache 2.0** across the whole stack
  (SDK, server, dashboard, adapters, channels). Pre-tag the README
  claimed a dual-license (MIT SDK + ELv2 server) that was never
  realized in pyproject.toml or package.json. The explicit patent
  grant in Apache 2.0 matters more for AI infra than the brevity of
  MIT.

### Added

**SDK & core**

- `await_human()` / `await_human_sync()` — the core primitive
- Python SDK with Pydantic schema validation
- TypeScript SDK (`awaithumans` on npm) with Zod schema validation
- Cross-platform idempotency key generation (works in Node, Bun, Deno, edge runtimes)
- Error classes with `what → why → fix → docs` shape in both SDKs
- In-memory test client (`awaithumans.testing`) for agent tests without a server

**Server**

- FastAPI app with SQLModel + Alembic migrations
- Task CRUD + state machine (created / notified / assigned /
  in_progress / submitted / completed / cancelled / timed_out)
- Long-poll endpoint for direct mode
- Timeout scheduler using indexed `timeout_at` column
- HMAC-signed webhook dispatch for completion callbacks
- Audit trail for every state transition and claim

**User directory**

- `User` model with synthetic ID primary key + nullable email / slack
  identifiers (at least one required)
- Admin API (`/api/admin/users` CRUD + password set/clear)
- CLI: `add-user`, `list-users`, `remove-user`, `set-password`,
  `bootstrap-operator`
- Task router (Option C — least-recently-assigned, transactional)
- Free-form `role` / `access_level` / `pool` labels for routing

**Auth**

- DB-backed per-user login with argon2id password hashing
- HMAC-signed session cookies (httponly, SameSite=Lax)
- First-run `/setup` bootstrap token flow (token printed to server
  log on startup when the users table is empty; one-shot)
- Admin bearer token as automation escape hatch
- Last-active-operator guard on delete / demote / deactivate
- Login timing equalization against unknown-user enumeration

**Dashboard**

- Next.js 16 + React 19 static export, bundled into the Python wheel
- Task queue, task detail, audit log, stats, settings pages
- User directory management UI with Slack workspace member picker
- First-run `/setup` wizard
- Brand palette: `#0A0A0A` / `#F5F5F5` / `#00E676`

**Channels**

- Slack: DM + channel broadcast with first-to-claim semantics,
  Block Kit form rendering, modal submission handling, OAuth install
  flow for multi-workspace, HMAC signature verification
- Email: Resend + SMTP transports, action buttons, confirmation
  pages, magic-link tokens signed with HKDF-derived key

**CLI & developer experience**

- `awaithumans dev` — one-command server + dashboard, auto-generates
  `PAYLOAD_KEY` for local dev
- `npx awaithumans dev` via `uv` — TypeScript developers never touch
  Python
- Docker image published to `ghcr.io/awaithumans/awaithumans` (multi-arch:
  linux/amd64, linux/arm64)
- `docker-compose.yml` with optional Postgres block
- Python quickstart example (`examples/quickstart/`)
- TypeScript quickstart example (`examples/quickstart-ts/`)

**Operational**

- Alembic migrations with date-based filenames (`YYYYMMDD_HHMM_slug.py`)
- GitHub Actions CI enforcing single-head alembic invariant
- Multi-arch Docker publish on tag + main push
- 306 automated tests across services, routes, and auth

### Known gaps — landing post-launch

- No rate limiting on login (argon2's CPU cost slows brute-force but
  doesn't stop it; proper limiter with Redis planned)
- No session invalidation on password change (outstanding sessions
  survive to expiry; `session_version` field planned)
- Production `PUBLIC_URL` must be HTTPS — currently a logged error,
  not a boot failure
- Temporal and LangGraph adapters are planned but not yet shipped
- AI verifier (Claude) adapter is planned but not yet shipped
