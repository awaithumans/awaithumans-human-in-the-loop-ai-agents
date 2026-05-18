"""Create the first operator from the command line.

Alternative to the `/setup` token flow — used when the server is
running in a non-interactive environment (docker-compose, CI) where
reading the setup URL from the server log and opening a browser
isn't practical.

Refuses if any user already exists. For subsequent operators, use
`awaithumans add-user --operator` instead.
"""

from __future__ import annotations

import asyncio

import typer

from awaithumans.cli.commands._session import with_session
from awaithumans.server.services.exceptions import UserAlreadyExistsError
from awaithumans.server.services.user_service import count_users, create_user


def bootstrap_operator(
    email: str = typer.Option(..., help="Operator's email address."),
    password: str | None = typer.Option(
        None,
        help="Operator password. Omit to prompt interactively with confirmation.",
    ),
    display_name: str | None = typer.Option(None, help="Human-readable name."),
) -> None:
    """Create the first operator (only runs when users table is empty).

    For automation. The `/setup` web flow is the recommended path for
    interactive first-run — this command is the escape hatch when
    operating headless.
    """
    # Prompt for password if not supplied — match the dashboard /setup UX.
    if password is None:
        password = typer.prompt("Operator password", hide_input=True, confirmation_prompt=True)
    if len(password) < 8:
        raise typer.BadParameter("Password must be at least 8 characters.")

    async def _run() -> None:
        async with with_session() as session:
            if await count_users(session) > 0:
                typer.echo(
                    "Error: setup has already been completed — use "
                    "`add-user --operator` to grant additional operator "
                    "access.",
                    err=True,
                )
                raise typer.Exit(code=1)

            try:
                user = await create_user(
                    session,
                    email=email,
                    display_name=display_name,
                    is_operator=True,
                    password=password,
                )
            except UserAlreadyExistsError as exc:
                # Same email reused between a failed first attempt and
                # this one, or a race with /setup. Same remediation —
                # surface and exit.
                typer.echo(f"Error: {exc.message}", err=True)
                raise typer.Exit(code=1) from exc

        typer.echo(f"Operator created: {user.email} ({user.id})")
        typer.echo("Log in at the dashboard with these credentials.")

    asyncio.run(_run())
