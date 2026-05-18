"""Idempotency-collision short-circuit (PR #72) — both Python adapters.

Pins the Stripe-style retry-returns-cached-response contract: when
`create_task` returns an existing terminal task (e.g. the dev re-ran
the example with the same idempotency key after a previous
completion), the adapter should:

  - For status=completed: validate the cached response and return it.
  - For status=timed_out / cancelled / verification_exhausted: raise
    the matching typed error.
  - NOT register a signal handler / NOT call interrupt() for a state
    transition that already happened — the webhook fired ages ago,
    so waiting for it would just timeout.

Without this, a developer testing the temporal example and running it
twice with the same payload would park their workflow on
`wait_condition` for the full `timeout_seconds` (15 min by default)
before getting a misleading `TaskTimeoutError`. Same shape exists in
the LangGraph adapter where `interrupt()` would never fire its
matching `Command(resume=...)`.

We test `_resolve_terminal` directly because the full surface of
`await_human` requires a live Temporal worker (covered in slower
integration tests) — but `_resolve_terminal` is what makes the
short-circuit work, and pinning its branches ensures every terminal
status maps to the right return / error.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from awaithumans.adapters.langgraph import _resolve_terminal as _resolve_terminal_lg
from awaithumans.adapters.temporal import _resolve_terminal as _resolve_terminal_t
from awaithumans.errors import (
    SchemaValidationError,
    TaskCancelledError,
    TaskTimeoutError,
    VerificationExhaustedError,
)


class RefundDecision(BaseModel):
    approved: bool
    notes: str | None = None


# ─── Temporal adapter ─────────────────────────────────────────────────


def test_temporal_resolve_completed_returns_validated_response() -> None:
    """Happy path: an already-completed task returns its cached
    response, validated against the schema."""
    task_record = {
        "status": "completed",
        "response": {"approved": True, "notes": "approved on a previous run"},
    }
    result = _resolve_terminal_t(
        "completed",
        task_record,
        RefundDecision,
        task="Approve refund",
        timeout_seconds=900,
    )
    assert isinstance(result, RefundDecision)
    assert result.approved is True
    assert result.notes == "approved on a previous run"


def test_temporal_resolve_completed_with_invalid_response_raises_schema_error() -> None:
    """If the cached response doesn't match the schema, surface a
    SchemaValidationError instead of returning a malformed object —
    same shape as the post-signal path."""
    task_record = {"status": "completed", "response": {"wrong_field": "bad"}}
    with pytest.raises(SchemaValidationError):
        _resolve_terminal_t(
            "completed",
            task_record,
            RefundDecision,
            task="Approve refund",
            timeout_seconds=900,
        )


def test_temporal_resolve_timed_out_raises_typed_error() -> None:
    with pytest.raises(TaskTimeoutError):
        _resolve_terminal_t(
            "timed_out",
            {"status": "timed_out", "response": None},
            RefundDecision,
            task="Approve refund",
            timeout_seconds=900,
        )


def test_temporal_resolve_cancelled_raises_typed_error() -> None:
    with pytest.raises(TaskCancelledError):
        _resolve_terminal_t(
            "cancelled",
            {"status": "cancelled", "response": None},
            RefundDecision,
            task="Approve refund",
            timeout_seconds=900,
        )


def test_temporal_resolve_verification_exhausted_carries_attempt_count() -> None:
    """The attempt counter helps the caller log how far the verifier
    got before giving up — pin that it survives the cached path."""
    task_record = {
        "status": "verification_exhausted",
        "response": None,
        "verification_attempt": 3,
    }
    with pytest.raises(VerificationExhaustedError) as exc_info:
        _resolve_terminal_t(
            "verification_exhausted",
            task_record,
            RefundDecision,
            task="Approve refund",
            timeout_seconds=900,
        )
    # Error stores the attempt number — assert it's the one we sent.
    assert "3" in str(exc_info.value)


def test_temporal_resolve_unknown_status_raises_runtime_error() -> None:
    """A novel terminal status (the server adds one we don't know
    about yet) should fail loud, not silently return None."""
    with pytest.raises(RuntimeError):
        _resolve_terminal_t(
            "made_up_status",
            {"status": "made_up_status"},
            RefundDecision,
            task="Approve refund",
            timeout_seconds=900,
        )


# ─── LangGraph adapter ────────────────────────────────────────────────
#
# Same shape as Temporal — the helper differs only in its module home,
# so we re-run the same matrix to pin both. Duplication here is
# cheaper than abstracting; if a regression slips in for one adapter
# only, the matrix still catches it.


def test_langgraph_resolve_completed_returns_validated_response() -> None:
    task_record = {
        "status": "completed",
        "response": {"approved": False, "notes": "fraud signal"},
    }
    result = _resolve_terminal_lg(
        "completed",
        task_record,
        RefundDecision,
        task="Approve refund",
        timeout_seconds=900,
    )
    assert result.approved is False
    assert result.notes == "fraud signal"


def test_langgraph_resolve_timed_out_raises_typed_error() -> None:
    with pytest.raises(TaskTimeoutError):
        _resolve_terminal_lg(
            "timed_out",
            {"status": "timed_out", "response": None},
            RefundDecision,
            task="Approve refund",
            timeout_seconds=900,
        )


def test_langgraph_resolve_cancelled_raises_typed_error() -> None:
    with pytest.raises(TaskCancelledError):
        _resolve_terminal_lg(
            "cancelled",
            {"status": "cancelled", "response": None},
            RefundDecision,
            task="Approve refund",
            timeout_seconds=900,
        )


def test_langgraph_resolve_verification_exhausted_carries_attempt_count() -> None:
    task_record = {
        "status": "verification_exhausted",
        "response": None,
        "verification_attempt": 5,
    }
    with pytest.raises(VerificationExhaustedError) as exc_info:
        _resolve_terminal_lg(
            "verification_exhausted",
            task_record,
            RefundDecision,
            task="Approve refund",
            timeout_seconds=900,
        )
    assert "5" in str(exc_info.value)
