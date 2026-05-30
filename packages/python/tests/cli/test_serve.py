"""Tests for the production ``awaithumans serve`` CLI entrypoint.

The command is the Docker / Container Apps entrypoint. The two
properties under test are:

1. It refuses to start unless storage is explicitly configured —
   ``AWAITHUMANS_DATABASE_URL`` or ``AWAITHUMANS_DB_PATH`` must be set
   in the environment. Silent fallback to ``.awaithumans/dev.db``
   was the failure mode that lost task data on Azure Container Apps
   (the Dockerfile's ``VOLUME`` directive isn't honored everywhere).
2. Once storage is set, it boots ``uvicorn.run`` against the
   FastAPI app factory with explicit production defaults. The
   real uvicorn loop is mocked so the test process doesn't actually
   bind a socket.

End-to-end "does it serve traffic" coverage is the integration
suite's job; here we only pin the entrypoint contract.

Note: we call ``serve(...)`` directly rather than going through
``typer.testing.CliRunner`` because the installed click 8.3 +
typer 0.15 combo trips a ``Parameter.make_metavar`` signature
mismatch when rendering ``--help``. Direct calls keep the test
focused on serve's own contract, not the typer/click plumbing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import typer

from awaithumans.cli.commands.serve import serve
from awaithumans.cli.main import app as cli_app


@pytest.fixture
def _no_storage_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wipe both storage env vars before each test so the fixture
    starts from a clean slate. Otherwise the test process's own env
    (e.g. a leaked AWAITHUMANS_DB_PATH from a previous test) would
    make refusal tests pass falsely."""
    monkeypatch.delenv("AWAITHUMANS_DATABASE_URL", raising=False)
    monkeypatch.delenv("AWAITHUMANS_DB_PATH", raising=False)


def test_serve_refuses_without_storage_env(
    _no_storage_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No DATABASE_URL, no DB_PATH → typer.Exit(1) with an actionable
    message on stderr.

    Both env names must appear in the error so an operator can find
    the right knob without leaving the terminal. The message also
    has to point at ``awaithumans dev`` as the local-dev alternative
    so nobody mistakes the refusal for a missing-command error.
    """
    with pytest.raises(typer.Exit) as excinfo:
        serve(host="0.0.0.0", port=3001, log_level="info")

    assert excinfo.value.exit_code == 1
    captured = capsys.readouterr()
    # typer.echo(..., err=True) writes to stderr.
    assert "AWAITHUMANS_DATABASE_URL" in captured.err
    assert "AWAITHUMANS_DB_PATH" in captured.err
    assert "awaithumans dev" in captured.err


def test_serve_accepts_when_database_url_set(
    _no_storage_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Postgres-style ``AWAITHUMANS_DATABASE_URL`` satisfies the
    storage guard. uvicorn.run is mocked so the test doesn't
    actually bind a port.
    """
    monkeypatch.setenv(
        "AWAITHUMANS_DATABASE_URL",
        "postgresql://u:p@localhost:5432/db",
    )
    monkeypatch.setenv("AWAITHUMANS_PAYLOAD_KEY", "x" * 43)

    fake_uvicorn = MagicMock()
    fake_app = MagicMock(name="FastAPIApp")

    with (
        patch.dict("sys.modules", {"uvicorn": fake_uvicorn}),
        patch("awaithumans.server.app.create_app", return_value=fake_app),
    ):
        serve(host="0.0.0.0", port=3001, log_level="info")

    fake_uvicorn.run.assert_called_once()
    args, kwargs = fake_uvicorn.run.call_args
    # uvicorn.run(app, host=..., port=..., log_level=...) — app is
    # positional; the rest are keyword.
    assert args[0] is fake_app
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 3001
    assert kwargs["log_level"] == "info"


def test_serve_accepts_when_db_path_set(
    _no_storage_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare ``AWAITHUMANS_DB_PATH`` (volume-backed SQLite) also
    satisfies the storage guard. This is the path the Dockerfile
    pre-configures, so the bare image must boot cleanly.
    """
    monkeypatch.setenv("AWAITHUMANS_DB_PATH", "/var/lib/awaithumans/data.db")
    monkeypatch.setenv("AWAITHUMANS_PAYLOAD_KEY", "x" * 43)

    fake_uvicorn = MagicMock()
    fake_app = MagicMock(name="FastAPIApp")

    with (
        patch.dict("sys.modules", {"uvicorn": fake_uvicorn}),
        patch("awaithumans.server.app.create_app", return_value=fake_app),
    ):
        serve(host="0.0.0.0", port=3001, log_level="info")

    fake_uvicorn.run.assert_called_once()


def test_serve_refuses_when_storage_env_is_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit-empty env vars (``AWAITHUMANS_DB_PATH=``) count as unset.

    Operators sometimes leave a shell-substituted env var unfilled
    (``AWAITHUMANS_DB_PATH=${DB_PATH:-}``) — that ends up as an empty
    string in the container, NOT an unset key. The guard must treat
    that as "operator didn't choose" to avoid the same silent-default
    failure mode an actually-unset key would cause.
    """
    monkeypatch.setenv("AWAITHUMANS_DATABASE_URL", "")
    monkeypatch.setenv("AWAITHUMANS_DB_PATH", "   ")  # whitespace-only

    with pytest.raises(typer.Exit) as excinfo:
        serve(host="0.0.0.0", port=3001, log_level="info")

    assert excinfo.value.exit_code == 1


def test_cli_registers_serve_command() -> None:
    """The serve command must be registered on the top-level Typer app.

    Catches the regression where main.py forgets to wire a new command
    (the command file exists, the import works, the function is
    callable, but ``typer`` never sees it). We inspect the Typer app's
    registered_commands list directly because CliRunner trips a
    click 8.3 / typer 0.15 compat bug on --help rendering.
    """
    command_names = {cmd.name or cmd.callback.__name__ for cmd in cli_app.registered_commands}
    assert "serve" in command_names, (
        f"Expected 'serve' in registered Typer commands; got {sorted(command_names)!r}"
    )
    # And `dev` must still be there too — the new command is additive,
    # not a replacement.
    assert "dev" in command_names
