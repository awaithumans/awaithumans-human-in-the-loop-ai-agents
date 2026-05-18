"""Add a user to the directory."""

from __future__ import annotations

import asyncio
import logging

import typer

from awaithumans.cli.commands._session import with_session
from awaithumans.server.services.exceptions import (
    UserAlreadyExistsError,
    UserNoAddressError,
)
from awaithumans.server.services.user_service import create_user

logger = logging.getLogger("awaithumans.cli")


def add_user(
    email: str | None = typer.Option(None, help="Email address (for email notifications + login)."),
    slack_team_id: str | None = typer.Option(
        None, help="Slack workspace ID (e.g. T01ABC234) — required if --slack-user-id is set."
    ),
    slack_user_id: str | None = typer.Option(
        None, help="Slack user ID within that workspace (e.g. U01XYZ789)."
    ),
    display_name: str | None = typer.Option(None, help="Human-readable name."),
    role: str | None = typer.Option(None, help="Routing role, e.g. 'kyc-reviewer'."),
    access_level: str | None = typer.Option(None, help="Routing access level, e.g. 'senior'."),
    pool: str | None = typer.Option(None, help="Routing pool, e.g. 'ops'."),
    is_operator: bool = typer.Option(False, "--operator", help="Grant dashboard admin rights."),
    password: str | None = typer.Option(
        None,
        help=(
            "Dashboard login password (min 8 chars). Leave blank for users who only receive tasks."
        ),
        prompt=False,
    ),
) -> None:
    """Add a user to the directory.

    At least one of `--email` or (`--slack-team-id` + `--slack-user-id`)
    must be set. Slack identifiers come in pairs because Slack user IDs
    are workspace-scoped.
    """
    if password is not None and len(password) < 8:
        raise typer.BadParameter("Password must be at least 8 characters.")

    async def _run() -> None:
        async with with_session() as session:
            try:
                user = await create_user(
                    session,
                    display_name=display_name,
                    email=email,
                    slack_team_id=slack_team_id,
                    slack_user_id=slack_user_id,
                    role=role,
                    access_level=access_level,
                    pool=pool,
                    is_operator=is_operator,
                    password=password,
                )
            except UserNoAddressError as exc:
                raise typer.BadParameter(str(exc)) from exc
            except UserAlreadyExistsError as exc:
                typer.echo(f"Error: {exc.message}", err=True)
                raise typer.Exit(code=1) from exc

        typer.echo(f"Added user: {user.id}")
        if user.display_name:
            typer.echo(f"  Name:  {user.display_name}")
        if user.email:
            typer.echo(f"  Email: {user.email}")
        if user.slack_team_id:
            typer.echo(f"  Slack: {user.slack_team_id}/{user.slack_user_id}")
        if user.role:
            typer.echo(f"  Role:  {user.role}")
        if user.access_level:
            typer.echo(f"  Level: {user.access_level}")
        if user.pool:
            typer.echo(f"  Pool:  {user.pool}")
        if user.is_operator:
            typer.echo("  Operator: yes")
        if user.password_hash:
            typer.echo("  Password: set")

    asyncio.run(_run())
