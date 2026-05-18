"""EmailTransport protocol + shared message types.

Every transport backend implements `send(message)` and raises
`EmailTransportError` on hard failures the caller should log.
Soft per-provider errors (rate limits, temporary outages) SHOULD be
retried by the caller; for v1 we don't retry — the notifier logs
the error and moves on, consistent with the Slack notifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol


class EmailTransportError(RuntimeError):
    """Transport failed to send. Callers log and continue."""


# RFC 5322 header injection: \r or \n in any header field lets an attacker
# inject additional headers (Bcc, Subject, etc.) or smuggle a body. We
# reject them outright at message construction time.
_HEADER_INJECTION_RE = re.compile(r"[\r\n]")


def _no_crlf(value: str, field_name: str) -> str:
    if _HEADER_INJECTION_RE.search(value):
        raise ValueError(
            f"Email header '{field_name}' contains CR/LF; rejected to prevent header injection."
        )
    return value


@dataclass
class EmailMessage:
    """One email to send. Validated against header-injection on construction."""

    to: str
    subject: str
    html: str
    text: str
    from_email: str
    from_name: str | None = None
    reply_to: str | None = None
    tags: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _no_crlf(self.to, "to")
        _no_crlf(self.subject, "subject")
        _no_crlf(self.from_email, "from_email")
        if self.from_name:
            _no_crlf(self.from_name, "from_name")
        if self.reply_to:
            _no_crlf(self.reply_to, "reply_to")

    def formatted_from(self) -> str:
        """Return `"Name" <email>` or bare email."""
        if self.from_name:
            return f'"{self.from_name}" <{self.from_email}>'
        return self.from_email


@dataclass(frozen=True)
class EmailSendResult:
    message_id: str | None
    transport: str


class EmailTransport(Protocol):
    """Send an email. Raises EmailTransportError on hard failure."""

    async def send(self, message: EmailMessage) -> EmailSendResult: ...  # pragma: no cover

    @property
    def name(self) -> str: ...  # pragma: no cover
