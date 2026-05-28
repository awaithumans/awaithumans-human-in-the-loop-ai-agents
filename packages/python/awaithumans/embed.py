"""SDK helper: mint embed tokens from partner backends.

Usage:
    from awaithumans import embed_token_sync

    embed = embed_token_sync(
        task_id=task.id,
        sub=f"acme:{current_user.id}",
        parent_origin="https://acme.com",
        api_key=os.environ["AH_SERVICE_KEY"],
    )
    return {"approval_url": embed.embed_url}

This module is part of the lightweight `awaithumans` SDK install (httpx
+ pydantic). Server-side mint endpoint (POST /api/embed/tokens) lives
in the `[server]` extra. See spec §4.4.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass

DEFAULT_SERVER = "http://localhost:3001"


@dataclass(frozen=True)
class EmbedTokenResult:
    embed_token: str
    embed_url: str
    expires_at: str


async def embed_token(
    *,
    task_id: str,
    parent_origin: str,
    api_key: str,
    sub: str | None = None,
    ttl_seconds: int | None = None,
    server_url: str | None = None,
) -> EmbedTokenResult:
    """Mint an embed token via the awaithumans server (async).

    `parent_origin` must be in the server's allowlist
    (AWAITHUMANS_EMBED_PARENT_ORIGINS env var) or the call returns 400.
    `ttl_seconds` is clamped to the server's [60, 3600] window.
    """
    _refuse_browser_runtime()
    base = (server_url or os.environ.get("AWAITHUMANS_URL") or DEFAULT_SERVER).rstrip("/")

    body: dict[str, object] = {
        "task_id": task_id,
        "parent_origin": parent_origin,
    }
    if sub is not None:
        body["sub"] = sub
    if ttl_seconds is not None:
        body["ttl_seconds"] = ttl_seconds

    # Deferred so `awaithumans` is import-safe inside a Temporal workflow
    # sandbox; httpx transitively pulls in urllib.request, which the
    # sandbox forbids at workflow replay time.
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            f"{base}/api/embed/tokens",
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        res.raise_for_status()
        data = res.json()
    return EmbedTokenResult(
        embed_token=data["embed_token"],
        embed_url=data["embed_url"],
        expires_at=data["expires_at"],
    )


def embed_token_sync(
    *,
    task_id: str,
    parent_origin: str,
    api_key: str,
    sub: str | None = None,
    ttl_seconds: int | None = None,
    server_url: str | None = None,
) -> EmbedTokenResult:
    """Sync wrapper around `embed_token` for non-async callers."""
    return asyncio.run(
        embed_token(
            task_id=task_id,
            parent_origin=parent_origin,
            api_key=api_key,
            sub=sub,
            ttl_seconds=ttl_seconds,
            server_url=server_url,
        )
    )


def _refuse_browser_runtime() -> None:
    """Best-effort dev-time guard.

    Service keys (`ah_sk_*`) must never appear in browser-side code.
    This helper runs in Python, so the only realistic browser context
    is Pyodide / Skulpt. When detected, write a stderr warning so
    leaked-key incidents have a fighting chance of being noticed.
    """
    if "pyodide" in sys.modules or "js" in sys.modules:
        sys.stderr.write(
            "\n[awaithumans] WARNING: service keys (ah_sk_...) "
            "are server-side only. Do not run embed_token from browser code.\n"
        )
