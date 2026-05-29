"""Types specific to AwaitVerify routing and SLA tiers.

Provider credentials and per-call extraction configs live in
`awaithumans.providers`. This module only holds the routing types
that flow through the underlying await_human call.
"""

from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel


class Priority(str, enum.Enum):
    """SLA priority for an AwaitVerify task.

    STANDARD is best-effort at the base per-page rate.

    HIGH triggers Express SLA: 2× the standard rate, with a 30-minute
    turnaround target firm during Mon-Fri 8am-8pm ET. Outside business
    hours we still prioritize Express tasks over standard but the
    30-minute number is best-effort, not guaranteed.
    """

    STANDARD = "standard"
    HIGH = "high"


class ManagedAssignment(BaseModel):
    """Routing target for managed AwaitVerify tasks.

    Sent as the `assign_to` field on the underlying await_human call.
    The server's router recognizes `managed == "awaitverify"` and
    dispatches to the AwaitVerify reviewer pool. The `priority`
    field selects the standard or Express queue.
    """

    managed: Literal["awaitverify"] = "awaitverify"
    priority: Priority = Priority.STANDARD


__all__ = [
    "Priority",
    "ManagedAssignment",
]
