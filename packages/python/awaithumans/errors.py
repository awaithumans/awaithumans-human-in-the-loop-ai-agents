"""Error classes for awaithumans.

Every error follows the what → why → fix → docs pattern.
"""

from awaithumans.utils.constants import DOCS_ROADMAP_URL, DOCS_TROUBLESHOOTING_URL


class AwaitHumansError(Exception):
    """Base error for all awaithumans errors."""

    def __init__(self, code: str, message: str, hint: str, docs_url: str) -> None:
        self.code = code
        self.hint = hint
        self.docs_url = docs_url
        full_message = f"{message}\n\n{hint}\n\nDocs: {docs_url}"
        super().__init__(full_message)


class TaskTimeoutError(AwaitHumansError):
    def __init__(self, task: str, timeout_seconds: int) -> None:
        super().__init__(
            code="TIMEOUT_EXCEEDED",
            message=f'Task "{task}" timed out after {timeout_seconds} seconds.',
            hint=(
                "No human completed the task. Check:\n"
                "  1. Is your notification channel configured? (AWAITHUMANS_SLACK_WEBHOOK)\n"
                "  2. Did the assigned human receive the notification?\n"
                "  3. Consider increasing timeout_seconds if humans need more time."
            ),
            docs_url=f"{DOCS_TROUBLESHOOTING_URL}#timeout",
        )


class TimeoutRangeError(AwaitHumansError):
    def __init__(self, timeout_seconds: int) -> None:
        super().__init__(
            code="TIMEOUT_OUT_OF_RANGE",
            message=(
                f"timeout_seconds must be between 60 (1 minute) and 2,592,000 (30 days). "
                f"Got: {timeout_seconds}."
            ),
            hint=(
                "awaithumans is designed for human response times.\n"
                "  Minimum: 60 seconds (1 minute)\n"
                "  Maximum: 2,592,000 seconds (30 days)\n"
                "For sub-minute timeouts, use a coroutine or a queue, not HITL."
            ),
            docs_url=f"{DOCS_TROUBLESHOOTING_URL}#timeout-range",
        )


class SchemaValidationError(AwaitHumansError):
    def __init__(self, field: str, details: str) -> None:
        super().__init__(
            code="SCHEMA_VALIDATION_FAILED",
            message=f"The {field} does not match the provided schema.",
            hint=(
                f"Validation error: {details}\n\n"
                f"Check that your {field} conforms to the {field}_schema you provided.\n"
                "All payloads and responses must be JSON-serializable."
            ),
            docs_url=f"{DOCS_TROUBLESHOOTING_URL}#schema-validation",
        )


class TaskAlreadyTerminalError(AwaitHumansError):
    def __init__(self, task_id: str, status: str) -> None:
        super().__init__(
            code="TASK_ALREADY_TERMINAL",
            message=f'Task "{task_id}" is already in terminal status "{status}".',
            hint=(
                "The task was completed, timed out, or cancelled before this action "
                "could be processed. This can happen in race conditions between the "
                "timeout and a human submission."
            ),
            docs_url=f"{DOCS_TROUBLESHOOTING_URL}#task-already-terminal",
        )


class VerificationExhaustedError(AwaitHumansError):
    def __init__(self, task: str, max_attempts: int) -> None:
        super().__init__(
            code="VERIFICATION_EXHAUSTED",
            message=f'Task "{task}" failed verification {max_attempts} times.',
            hint=(
                "The human's response was rejected by the verifier on every attempt.\n"
                "Check your verifier instructions — they may be too strict.\n"
                "Consider increasing max_attempts or adjusting the verification criteria."
            ),
            docs_url=f"{DOCS_TROUBLESHOOTING_URL}#verification-exhausted",
        )


class MarketplaceNotAvailableError(AwaitHumansError):
    def __init__(self) -> None:
        super().__init__(
            code="MARKETPLACE_NOT_AVAILABLE",
            message=(
                "The workforce marketplace (assign_to=MarketplaceAssignment) is not yet available."
            ),
            hint=(
                "The marketplace is coming in a future release. For now, assign tasks "
                "to specific humans, pools, or roles."
            ),
            docs_url=f"{DOCS_ROADMAP_URL}#marketplace",
        )


# ─── Runtime errors returned by the server ──────────────────────────────
#
# These mirror the TypeScript SDK's error classes one-for-one (see
# packages/typescript-sdk/src/errors.ts). Cross-language parity is a
# CLAUDE.md §7 hard rule — Python users `except TaskNotFoundError`,
# TypeScript users `catch (e instanceof TaskNotFoundError)`, both see
# the same wire `code` field, both can write transport-specific recovery
# without conditionals on string codes.


class TaskNotFoundError(AwaitHumansError):
    def __init__(self, task_id: str) -> None:
        super().__init__(
            code="TASK_NOT_FOUND",
            message=f"Task '{task_id}' not found on the server.",
            hint=(
                "The task may have been deleted or the server was restarted with a fresh database."
            ),
            docs_url=f"{DOCS_TROUBLESHOOTING_URL}#task-not-found",
        )


class TaskCancelledError(AwaitHumansError):
    def __init__(self, task: str) -> None:
        super().__init__(
            code="TASK_CANCELLED",
            message=f"Task '{task}' was cancelled.",
            hint=(
                "The task was cancelled by an admin or another agent before a human completed it."
            ),
            docs_url=f"{DOCS_TROUBLESHOOTING_URL}#task-cancelled",
        )


class TaskCreateError(AwaitHumansError):
    def __init__(self, status: int, server_body: str) -> None:
        super().__init__(
            code="TASK_CREATE_FAILED",
            message=f"Failed to create task on the server (HTTP {status}).",
            hint=f"Server response: {server_body[:500]}",
            docs_url=f"{DOCS_TROUBLESHOOTING_URL}#task-create-failed",
        )


class PollError(AwaitHumansError):
    def __init__(self, task_id: str, status: int, server_body: str) -> None:
        super().__init__(
            code="POLL_FAILED",
            message=f"Failed to poll task '{task_id}' (HTTP {status}).",
            hint=f"Server response: {server_body[:500]}",
            docs_url=f"{DOCS_TROUBLESHOOTING_URL}#poll-failed",
        )


class ServerUnreachableError(AwaitHumansError):
    def __init__(self, server_url: str, cause: object) -> None:
        super().__init__(
            code="SERVER_UNREACHABLE",
            message=f"Could not reach the awaithumans server at {server_url}.",
            hint=(
                "Check:\n"
                "  1. Is the server running? (awaithumans dev)\n"
                "  2. Is the URL correct? Override with `server_url=` or "
                "AWAITHUMANS_URL.\n"
                f"  3. Underlying error: {cause!s}"
            ),
            docs_url=f"{DOCS_TROUBLESHOOTING_URL}#server-unreachable",
        )
