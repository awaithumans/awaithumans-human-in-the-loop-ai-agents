"""`awaithumans revoke-service-key <id>`

Mark the given key revoked. Idempotent; revoking an already-revoked
key prints success without error.
"""

from __future__ import annotations

import asyncio

import typer

from awaithumans.cli.commands._session import with_session
from awaithumans.server.services.exceptions import ServiceKeyNotFoundError
from awaithumans.server.services.service_key_service import (
    revoke_service_key as _revoke,
)


def revoke_service_key(
    key_id: str = typer.Argument(..., help="ID from `list-service-keys`."),
) -> None:
    """Revoke a service API key. Idempotent."""

    async def _run() -> None:
        async with with_session() as session:
            try:
                row = await _revoke(session, key_id)
            except ServiceKeyNotFoundError as e:
                typer.echo(
                    typer.style(f"✗ no such key: {key_id}", fg=typer.colors.RED),
                    err=True,
                )
                raise typer.Exit(code=1) from e

        typer.echo(typer.style(f"✓ revoked {row.name} ({row.id})", fg=typer.colors.GREEN))

    asyncio.run(_run())
