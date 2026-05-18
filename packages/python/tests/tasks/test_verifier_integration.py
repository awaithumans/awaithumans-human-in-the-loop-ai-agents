"""Verifier integration with the task-completion path.

These tests pin the contract between `complete_task` and the
verifier subsystem WITHOUT making real LLM calls. They monkeypatch
`run_verifier` to return canned VerifierResult values and assert the
state machine transitions correctly:

  - no verifier_config → straight COMPLETED (regression for the
    pre-verifier behaviour)
  - verifier passes → COMPLETED, verifier_result stored
  - verifier fails with attempts left → REJECTED (non-terminal),
    attempt counter bumped, can resubmit
  - verifier fails with attempts exhausted → VERIFICATION_EXHAUSTED
    (terminal), agent unblocked
  - NL path: `raw_input` + verifier `parsed_response` → that parsed
    value becomes the task's stored response
  - provider failure does NOT burn an attempt — the LLM never rendered
    a verdict so the human gets a fresh shot

Mocking `run_verifier` (not the providers) keeps these tests at the
service-layer boundary."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from awaithumans.server.db.models import (  # noqa: F401 — register models
    AuditEntry,
    EmailSenderIdentity,
    SlackInstallation,
    Task,
)
from awaithumans.server.services import task_verifier
from awaithumans.server.services.exceptions import VerifierProviderError
from awaithumans.server.services.task_service import complete_task, create_task
from awaithumans.types import TaskStatus, VerifierResult


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _verifier_cfg(max_attempts: int = 3) -> dict:
    return {
        "provider": "claude",
        "model": "claude-sonnet-4-5",
        "instructions": "Check the decision is consistent.",
        "max_attempts": max_attempts,
        "api_key_env": "ANTHROPIC_API_KEY",
    }


async def _make_task(session: AsyncSession, **overrides) -> Task:
    task, _ = await create_task(
        session,
        task="Approve refund",
        payload={"amount": 100},
        payload_schema={"type": "object"},
        response_schema={"type": "object"},
        timeout_seconds=900,
        idempotency_key=f"key-{id(overrides)}",
        **overrides,
    )
    return task


@pytest.mark.asyncio
async def test_complete_task_without_verifier_goes_straight_to_completed(
    session: AsyncSession,
) -> None:
    task = await _make_task(session)
    completed = await complete_task(session, task_id=task.id, response={"approved": True})
    assert completed.status == TaskStatus.COMPLETED
    assert completed.verifier_result is None
    assert completed.verification_attempt == 0


@pytest.mark.asyncio
async def test_verifier_pass_marks_completed_and_stores_result(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = await _make_task(session, verifier_config=_verifier_cfg())

    async def fake(_cfg, _ctx):
        return VerifierResult(passed=True, reason="Looks consistent.")

    monkeypatch.setattr(task_verifier, "run_verifier", fake)

    completed = await complete_task(session, task_id=task.id, response={"approved": True})
    assert completed.status == TaskStatus.COMPLETED
    assert completed.verification_attempt == 1
    assert completed.verifier_result is not None
    assert completed.verifier_result["passed"] is True
    assert completed.completed_at is not None


@pytest.mark.asyncio
async def test_verifier_reject_with_attempts_left_marks_rejected(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = await _make_task(session, verifier_config=_verifier_cfg(max_attempts=3))

    async def fake(_cfg, _ctx):
        return VerifierResult(passed=False, reason="Decision conflicts with policy.")

    monkeypatch.setattr(task_verifier, "run_verifier", fake)

    rejected = await complete_task(session, task_id=task.id, response={"approved": True})
    assert rejected.status == TaskStatus.REJECTED
    assert rejected.verification_attempt == 1
    # REJECTED is non-terminal — completed_at must NOT be stamped or the
    # dashboard would render this as done. The agent's long-poll keeps
    # waiting; the human can resubmit and run another attempt.
    assert rejected.completed_at is None
    assert rejected.verifier_result["reason"] == "Decision conflicts with policy."


@pytest.mark.asyncio
async def test_verifier_reject_can_be_resubmitted_and_attempt_advances(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REJECTED is non-terminal; a second submit runs another verifier
    attempt. This pins that the partial-unique idempotency index allows
    re-completion of REJECTED tasks (regression target — terminal-only
    gating works, REJECTED must stay outside the terminal set)."""
    task = await _make_task(session, verifier_config=_verifier_cfg(max_attempts=3))

    verdicts = iter(
        [
            VerifierResult(passed=False, reason="Try again with notes."),
            VerifierResult(passed=True, reason="OK now."),
        ]
    )

    async def fake(_cfg, _ctx):
        return next(verdicts)

    monkeypatch.setattr(task_verifier, "run_verifier", fake)

    first = await complete_task(session, task_id=task.id, response={"approved": True})
    assert first.status == TaskStatus.REJECTED
    assert first.verification_attempt == 1

    second = await complete_task(
        session, task_id=task.id, response={"approved": True, "notes": "seen"}
    )
    assert second.status == TaskStatus.COMPLETED
    assert second.verification_attempt == 2


@pytest.mark.asyncio
async def test_verifier_exhausted_marks_terminal(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = await _make_task(session, verifier_config=_verifier_cfg(max_attempts=2))

    async def fake(_cfg, _ctx):
        return VerifierResult(passed=False, reason="Still wrong.")

    monkeypatch.setattr(task_verifier, "run_verifier", fake)

    first = await complete_task(session, task_id=task.id, response={"approved": True})
    assert first.status == TaskStatus.REJECTED
    assert first.verification_attempt == 1

    # Second attempt — equals max_attempts → exhausted (terminal).
    second = await complete_task(session, task_id=task.id, response={"approved": True})
    assert second.status == TaskStatus.VERIFICATION_EXHAUSTED
    assert second.verification_attempt == 2


@pytest.mark.asyncio
async def test_verifier_nl_parsed_response_replaces_stored_response(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = await _make_task(session, verifier_config=_verifier_cfg())

    async def fake(_cfg, _ctx):
        # The structured value the verifier extracted from raw_input.
        return VerifierResult(
            passed=True,
            reason="Parsed approval from NL.",
            parsed_response={"approved": True, "note": "looks good"},
        )

    monkeypatch.setattr(task_verifier, "run_verifier", fake)

    completed = await complete_task(
        session,
        task_id=task.id,
        response={},  # form was empty — NL path
        raw_input="approve, looks good",
    )
    assert completed.status == TaskStatus.COMPLETED
    assert completed.response == {"approved": True, "note": "looks good"}


@pytest.mark.asyncio
async def test_provider_failure_does_not_burn_an_attempt(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distinguish 'LLM said this is bad' (counts) from 'LLM call blew
    up' (doesn't count). The human gets a fresh shot once the operator
    fixes the config / API key."""
    task = await _make_task(session, verifier_config=_verifier_cfg())

    async def fake(_cfg, _ctx):
        raise VerifierProviderError("claude", "rate limit exceeded")

    monkeypatch.setattr(task_verifier, "run_verifier", fake)

    with pytest.raises(VerifierProviderError):
        await complete_task(session, task_id=task.id, response={"approved": True})

    # Re-fetch — task should still be non-terminal with attempt=0.
    await session.refresh(task)
    assert task.status != TaskStatus.COMPLETED
    assert task.status != TaskStatus.VERIFICATION_EXHAUSTED
    assert task.verification_attempt == 0
