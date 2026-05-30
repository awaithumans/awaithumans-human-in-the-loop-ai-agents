"""Regression: lifespan startup failures must surface a traceback.

uvicorn's lifespan protocol catches exceptions raised during startup
and exits the worker without printing them. Operators on Azure
Container Apps and similar runtimes saw container exit code 3 with
only ``INFO: Waiting for application startup.`` in the logs — no
indication of which import / config / DB call actually blew up. The
fix in ``server/app.py`` wraps the lifespan body so any startup
exception is logged at ERROR level (with ``traceback.format_exc()``)
and the same traceback is also written directly to stderr, so at
least one of the two channels survives whatever the runtime does
with stdout/stderr/log filtering.

These tests pin both delivery channels: the ``logger.error`` call
(verified by spying on the logger directly, which is robust to
global logging-config drift between tests) and the stderr write
(verified via ``capsys``). If either disappears, the silent-exit
regression returns.
"""

from __future__ import annotations

import secrets
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from awaithumans.server import app as app_module
from awaithumans.server.app import create_app, lifespan
from awaithumans.server.core import encryption
from awaithumans.server.core.config import settings


@pytest.fixture
def _valid_payload_key() -> Any:
    """Make sure the PAYLOAD_KEY pre-check passes so the test can
    exercise the post-key startup path (init_db etc.)."""
    original = settings.PAYLOAD_KEY
    settings.PAYLOAD_KEY = secrets.token_urlsafe(32)
    encryption.reset_key_cache()
    yield
    settings.PAYLOAD_KEY = original
    encryption.reset_key_cache()


def _format_log_call(call: Any) -> str:
    """Join positional args of a logger.error mock call so we can grep
    the formatted message text for substrings. The fix passes the
    traceback as a positional arg (``logger.error("...\n%s", tb)``)."""
    return " ".join(str(a) for a in call.args)


async def test_init_db_failure_logs_traceback(
    _valid_payload_key: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``init_db()`` raising surfaces a full traceback via
    ``logger.error`` AND a direct stderr write.

    The exception class is unique to this test (so we know it's the
    one we injected, not unrelated failure noise) and the message
    is distinctive enough to substring-match. Both channels must
    carry the traceback — they are two independent safety nets
    and the silent-exit regression was bad enough that we don't
    trust either one alone given how badly the failure mode
    degraded before the fix.
    """
    boom_message = "SIMULATED_INIT_DB_FAILURE_12345"

    class _SimulatedInitFailureError(RuntimeError):
        pass

    fake_init = AsyncMock(side_effect=_SimulatedInitFailureError(boom_message))

    app = create_app()

    with (
        patch("awaithumans.server.app.init_db", fake_init),
        patch.object(app_module.logger, "error") as mock_error,
        pytest.raises(_SimulatedInitFailureError) as excinfo,
    ):
        async with lifespan(app):
            pytest.fail("lifespan body should not yield when init_db raises")

    # The original exception must propagate unchanged so uvicorn still
    # exits non-zero. A swallowed re-raise would be just as bad as
    # the original silent-exit regression.
    assert boom_message in str(excinfo.value)

    # logger.error must have been called at least once with the
    # lifespan-failure marker AND the traceback substance. The class
    # name proves traceback.format_exc() was used (str(exc) wouldn't
    # include the class). The boom_message proves the chained cause
    # is intact.
    error_payloads = [_format_log_call(c) for c in mock_error.call_args_list]
    combined = "\n".join(error_payloads)
    assert any("Lifespan startup failed" in p for p in error_payloads), (
        f"Expected 'Lifespan startup failed' in a logger.error call; got: {combined!r}"
    )
    assert "_SimulatedInitFailureError" in combined, (
        f"Expected traceback class in logger.error args; got: {combined!r}"
    )
    assert boom_message in combined, (
        f"Expected boom message in logger.error args; got: {combined!r}"
    )

    # stderr carries the direct print fallback. Same substring checks
    # — both channels must independently survive log-config drift.
    captured = capsys.readouterr()
    assert "_SimulatedInitFailureError" in captured.err, (
        f"Expected traceback class on stderr; got: {captured.err!r}"
    )
    assert boom_message in captured.err, f"Expected boom message on stderr; got: {captured.err!r}"


async def test_first_run_setup_url_failure_logs_traceback(
    _valid_payload_key: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failure inside ``_first_run_setup_url`` (e.g. count_users raising
    because the schema is unexpectedly empty after init_db succeeded)
    must also be surfaced. This covers post-DB-init startup steps,
    which are the trickiest to debug remotely because they happen
    after the first reassuring "Database initialized" log line.

    ``init_db`` is stubbed to a no-op so the alembic migration runner
    doesn't fire and reshape global logging via ``fileConfig`` mid-test
    — that interaction is what makes capsys-on-stdout assertions
    flaky across test orderings.
    """
    boom_message = "SIMULATED_FIRST_RUN_FAILURE_67890"

    class _SimulatedFirstRunFailureError(RuntimeError):
        pass

    fake_first_run = AsyncMock(side_effect=_SimulatedFirstRunFailureError(boom_message))

    app = create_app()

    with (
        patch("awaithumans.server.app.init_db", AsyncMock(return_value=None)),
        patch("awaithumans.server.app._first_run_setup_url", fake_first_run),
        patch.object(app_module.logger, "error") as mock_error,
        pytest.raises(_SimulatedFirstRunFailureError),
    ):
        async with lifespan(app):
            pytest.fail("lifespan should not yield when first-run URL lookup fails")

    error_payloads = [_format_log_call(c) for c in mock_error.call_args_list]
    combined = "\n".join(error_payloads)
    assert "_SimulatedFirstRunFailureError" in combined
    assert boom_message in combined

    captured = capsys.readouterr()
    assert "_SimulatedFirstRunFailureError" in captured.err
    assert boom_message in captured.err
