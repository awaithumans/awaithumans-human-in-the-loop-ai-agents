"""Pre-flight checks for common misconfigurations."""

from __future__ import annotations

import typer


def doctor() -> None:
    """Run pre-flight checks against the current environment."""
    typer.echo("awaithumans doctor — checking environment")
    typer.echo("─" * 45)
    typer.echo("\n0 checks defined yet.\n")