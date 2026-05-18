"""
awaithumans — The human layer for AI agents.

Your agents already await promises. Now they can await humans.

Usage (async):
    from awaithumans import await_human

    result = await await_human(
        task="Approve this refund?",
        payload_schema=RefundPayload,
        payload=RefundPayload(amount=240, customer="cus_123"),
        response_schema=RefundDecision,
        timeout_seconds=900,
    )

Usage (sync):
    from awaithumans import await_human_sync

    result = await_human_sync(task=..., ...)
"""

from __future__ import annotations

__version__ = "0.1.4"

from awaithumans.client import await_human, await_human_sync
from awaithumans.embed import EmbedTokenResult, embed_token, embed_token_sync
from awaithumans.errors import (
    AwaitHumansError,
    MarketplaceNotAvailableError,
    PollError,
    SchemaValidationError,
    ServerUnreachableError,
    TaskAlreadyTerminalError,
    TaskCancelledError,
    TaskCreateError,
    TaskNotFoundError,
    TaskTimeoutError,
    TimeoutRangeError,
    VerificationExhaustedError,
)
from awaithumans.types import (
    AssignTo,
    AwaitHumanOptions,
    HumanIdentity,
    TaskRecord,
    TaskStatus,
    VerificationContext,
    VerifierConfig,
    VerifierResult,
)

__all__ = [
    "await_human",
    "await_human_sync",
    "embed_token",
    "embed_token_sync",
    "EmbedTokenResult",
    "AssignTo",
    "AwaitHumanOptions",
    "HumanIdentity",
    "TaskRecord",
    "TaskStatus",
    "VerificationContext",
    "VerifierConfig",
    "VerifierResult",
    "AwaitHumansError",
    "MarketplaceNotAvailableError",
    "PollError",
    "SchemaValidationError",
    "ServerUnreachableError",
    "TaskAlreadyTerminalError",
    "TaskCancelledError",
    "TaskCreateError",
    "TaskNotFoundError",
    "TaskTimeoutError",
    "TimeoutRangeError",
    "VerificationExhaustedError",
]
