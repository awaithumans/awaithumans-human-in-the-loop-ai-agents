"""Demo request/response schemas (route layer only).

Pydantic models exposed by the v2 ``/demo/*`` routes. Keeping them here
(rather than in ``routes/demo.py``) follows the project rule that
schemas live under ``server/schemas/`` so route files contain only
handlers.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from awaithumans.server.db.models import DemoStatus


class StartDemoResponse(BaseModel):
    """Response body of ``POST /demo/start``.

    The wizard reads ``pending_field_names`` to decide which fields to
    leave as "awaiting reviewer" placeholders and which to render
    confirmed. ``ai_result`` carries the raw values dict; the wizard
    cross-references it against ``pending_field_names`` to drive the
    live result UI.
    """

    demo_id: str
    ai_result: dict[str, Any]
    pending_field_names: list[str]


class DemoStatusResponse(BaseModel):
    """Response body of ``GET /demo/{demo_id}/status``.

    The wizard polls this endpoint to drive the live result UI as the
    reviewer corrects flagged fields. ``claimed_by`` surfaces from the
    linked AwaitVerify task (``Task.assigned_to_email``); the wizard
    uses it to render "claimed by Alice from review pool" once a
    reviewer picks the task up.
    """

    demo_id: str
    status: DemoStatus
    pending_field_names: list[str]
    field_corrections: dict[str, Any]
    awaitverify_task_id: str | None
    claimed_by: str | None
    email_sent_at_unix: int | None
