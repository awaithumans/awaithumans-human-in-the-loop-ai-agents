"""DemoStatus enum.

Split out so it can be imported by both the SQLModel and services
without circular imports. Values cover the full lifecycle:

- extracting: provider call in flight
- routing: done extracting, creating the AwaitVerify task
- awaiting_claim: task created, no reviewer claimed it yet
- claimed: reviewer has claimed but hasn't submitted any field
- partially_done: some flagged fields submitted, not all
- review_complete: every flagged field corrected
- email_sent: receipt email delivered
- email_failed: receipt email send retries exhausted
- routing_failed: couldn't create the AwaitVerify task (immediate fallback fires)
- fallback_sent: 72h fallback email delivered because reviewer never finished
"""

from __future__ import annotations

import enum


class DemoStatus(str, enum.Enum):
    EXTRACTING = "extracting"
    ROUTING = "routing"
    AWAITING_CLAIM = "awaiting_claim"
    CLAIMED = "claimed"
    PARTIALLY_DONE = "partially_done"
    REVIEW_COMPLETE = "review_complete"
    EMAIL_SENT = "email_sent"
    EMAIL_FAILED = "email_failed"
    ROUTING_FAILED = "routing_failed"
    FALLBACK_SENT = "fallback_sent"
