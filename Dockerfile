# syntax=docker/dockerfile:1.7

# ──────────────────────────────────────────────────────────────────────
# awaithumans — server + bundled dashboard in a single image.
#
# Multi-stage build:
#
#   1. `dashboard-builder` — Node + npm, runs scripts/build-bundled.sh
#       which stashes app/api/, runs `next build` with static-export
#       flags, and drops the output into the Python package at
#       awaithumans/dashboard_dist/.
#
#   2. `wheel-builder` — Python + hatchling, takes the tree with the
#       dashboard bundled in and produces a wheel (non-Python files
#       inside the package ship automatically).
#
#   3. `runtime` — slim Python, installs the wheel with [server]
#       extras, runs as a non-root user, exposes :3001.
#
# Users run `docker run -p 3001:3001 ghcr.io/awaithumans/awaithumans`
# and get API + UI on the same port. No Python toolchain needed.
# ──────────────────────────────────────────────────────────────────────

# ── Stage 1: dashboard ──────────────────────────────────────────────
FROM --platform=$BUILDPLATFORM node:22-slim AS dashboard-builder

WORKDIR /src

# Lockfile first — lets Docker cache `npm ci` across source changes.
# The dashboard package.json is standalone; no cross-workspace deps,
# so we don't need to copy other packages' manifests here.
COPY packages/dashboard/package.json packages/dashboard/package-lock.json packages/dashboard/

RUN cd packages/dashboard && npm ci --prefer-offline --no-audit

# Copy everything except what's in .dockerignore. Turbopack's tsconfig
# resolution is fussier than tsc's about directory structure; giving
# it the full tree (including root tsconfig.base.json and the whole
# packages/) keeps paths identical to how devs run the script locally.
COPY . .

RUN chmod +x scripts/build-bundled.sh && ./scripts/build-bundled.sh

# ── Stage 2: wheel ──────────────────────────────────────────────────
FROM --platform=$BUILDPLATFORM python:3.12-slim AS wheel-builder

WORKDIR /src

# Tree with the dashboard already bundled into the package. We need
# the whole Python package for hatchling to walk.
COPY packages/python/ packages/python/
COPY --from=dashboard-builder /src/packages/python/awaithumans/dashboard_dist \
     packages/python/awaithumans/dashboard_dist

RUN pip install --no-cache-dir build==1.2.2 \
 && cd packages/python \
 && python -m build --wheel

# ── Stage 3: runtime ────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# psycopg / asyncpg for Postgres deployments need libpq; install it
# preemptively since the [server] extras include asyncpg.
# libffi for cryptography; libssl for TLS.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpq5 \
      libffi8 \
      libssl3 \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install the wheel + server extras into the system Python. Slim
# image → no venv overhead needed.
# Copy the whole dist/ dir then install by wildcard — Docker's COPY
# with a glob source sometimes resolves to nothing when the target
# stage is untagged; grabbing the dir avoids that edge case.
COPY --from=wheel-builder /src/packages/python/dist /tmp/dist
# `pip install <path>[extra]` reads `[extra]` as literal, but bash
# globs `[...]` as a character class — `*.whl[server]` would expand
# to nothing. Resolve the wheel path first, then concat the extras.
RUN WHEEL=$(ls /tmp/dist/*.whl | head -1) \
 && pip install --no-cache-dir \
      "${WHEEL}[server,verifier-claude,verifier-openai,verifier-gemini,verifier-azure]" \
 && rm -rf /tmp/dist
# Why all verifier extras? Without them, sending a verifier config to the
# server fails with "Verifier provider 'X' requires the [verifier-X]
# extra. Install with: pip install awaithumans[verifier-X]" — which the
# user can't do because the server lives inside an image. Shipping all
# four extras costs ~35 MB on top of the ~200 MB base. Users who care
# about image size can build a custom image picking only the extras
# they actually use. See #142.

# Non-root runtime user. SQLite + any writes go to /var/lib/awaithumans.
RUN useradd --system --uid 10001 --home-dir /var/lib/awaithumans awaithumans \
 && mkdir -p /var/lib/awaithumans \
 && chown -R awaithumans:awaithumans /var/lib/awaithumans

USER awaithumans
WORKDIR /var/lib/awaithumans

# Container config: bind all interfaces, persist DB to the volume.
ENV AWAITHUMANS_HOST=0.0.0.0 \
    AWAITHUMANS_PORT=3001 \
    AWAITHUMANS_DB_PATH=/var/lib/awaithumans/awaithumans.db

EXPOSE 3001
VOLUME ["/var/lib/awaithumans"]

# /api/health is an unauthenticated route (public even when dashboard
# auth is on), so healthcheck works in every config.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:3001/api/health', timeout=3)" \
      || exit 1

# Production entrypoint. `awaithumans serve` is the prod-mode CLI:
# it runs uvicorn against the FastAPI app factory with explicit defaults
# and refuses to start unless storage (DATABASE_URL or DB_PATH) is set.
# The Dockerfile pre-sets AWAITHUMANS_DB_PATH above, so the bare image
# boots into single-node SQLite-on-volume mode — operators who outgrow
# that point AWAITHUMANS_DATABASE_URL at Postgres and the env-var check
# still passes. For local hacking on the source tree, use
# `awaithumans dev` instead (auto-generates keys, tokens, and a SQLite
# path under .awaithumans/).
CMD ["awaithumans", "serve"]
