"""`awaithumans list-service-keys [--all]`

Print service API keys ordered by creation time. Revoked keys are
hidden by default; pass --all to include them.
"""

from __future__ import annotations

import asyncio

import typer

from awaithumans.cli.commands._session import with_session
from awaithumans.server.services.service_key_service import (
    list_service_keys as _list,
)


def list_service_keys(
    include_revoked: bool = typer.Option(False, "--all", help="Include revoked keys."),
) -> None:
    """List service API keys."""

    async def _run() -> None:
        async with with_session() as session:
            rows = await _list(session, include_revoked=include_revoked)

        if not rows:
            typer.echo(
                "No service keys yet. "
                "Create one with `awaithumans create-service-key --name <name>`."
            )
            return

        header = f"{'ID':<32}  {'NAME':<30}  {'PREFIX':<14}  {'CREATED':<25}  STATUS"
        typer.echo(header)
        typer.echo("-" * len(header))
        for r in rows:
            status = "revoked" if r.revoked_at else "active"
            typer.echo(
                f"{r.id:<32}  {r.name[:30]:<30}  {r.key_prefix:<14}  "
                f"{r.created_at.isoformat(timespec='seconds'):<25}  {status}"
            )

    asyncio.run(_run())
