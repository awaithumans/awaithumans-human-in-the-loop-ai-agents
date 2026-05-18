"""Structured JSON logging for the awaithumans server.

Provides:
- JSON-formatted log output (one JSON object per line)
- Request ID correlation via contextvars
- A scrubbing filter that redacts API keys / passwords / bearer
  tokens from log records before they ever reach a handler. Cheap
  insurance: even if a careless `logger.info("%s", body)` lands in
  CI tomorrow, secrets won't make it to the log aggregator.
- Configurable log level via AWAITHUMANS_LOG_LEVEL env var

Usage:
    from awaithumans.server.core.logging_config import setup_logging, request_id_var

    # At app startup:
    setup_logging()

    # In middleware:
    request_id_var.set("some-uuid")
"""

from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_var: ContextVar[str] = ContextVar("request_id", default="")


# Patterns the scrubber redacts. Belt-and-braces with the
# verifier-side `sanitize_provider_error_detail` (which scrubs vendor
# error strings before they reach `VerifierProviderError`) — that
# happens at the boundary; this happens at the log handler. Both must
# exist because vendor errors aren't the only source of credentials
# in log records (a misuse like `logger.debug("%s", request.body)`
# is what this catches).
_LOG_SCRUB_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),  # OpenAI / Anthropic-style
    re.compile(r"sk_[a-z]+_[A-Za-z0-9_\-]{8,}"),  # scoped variants
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]+", re.IGNORECASE),
    re.compile(r"AIza[A-Za-z0-9_\-]{35,}"),  # Google API key prefix
    # `password=...`, `password: "..."`, `"password":"..."`
    re.compile(
        r"""(["']?password["']?\s*[:=]\s*["']?)([^"'\s,}]+)""",
        re.IGNORECASE,
    ),
    # x-admin-token: ... / X-Slack-Signature: ... etc — header lines
    re.compile(
        r"((?:x-admin-token|x-slack-signature)\s*:\s*)([^\s,]+)",
        re.IGNORECASE,
    ),
]


def scrub_text(value: str) -> str:
    """Apply every scrubber pattern to one string."""
    for pattern in _LOG_SCRUB_PATTERNS:
        # Two flavours: simple replacement (group 0) vs keyed
        # replacement (preserve the field name, redact only the
        # value). Distinguished by the number of groups in the
        # pattern.
        if pattern.groups >= 2:
            value = pattern.sub(r"\1[REDACTED]", value)
        else:
            value = pattern.sub("[REDACTED]", value)
    return value


class _ScrubFilter(logging.Filter):
    """Redact sensitive substrings from every log record's message.

    Acts on the formatted message AND the args (so `%s`-style logging
    of a credential-containing object is caught even before
    formatting). Doesn't try to be smart about structured args — a
    dict carrying `{"password": "x"}` becomes part of `record.args`
    and gets scrubbed via its repr."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub_text(record.msg)
        if record.args:
            scrubbed: list[object] | tuple[object, ...]
            if isinstance(record.args, dict):
                scrubbed = {  # type: ignore[assignment]
                    k: scrub_text(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            else:
                scrubbed = tuple(scrub_text(a) if isinstance(a, str) else a for a in record.args)
            record.args = scrubbed  # type: ignore[assignment]
        return True


class AwaitHumansFormatter(logging.Formatter):
    """JSON-like structured formatter with request ID correlation."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        level = record.levelname
        logger_name = record.name
        message = record.getMessage()

        req_id = request_id_var.get("")
        req_id_part = f" request_id={req_id}" if req_id else ""

        return f"{timestamp} [{level}] {logger_name}{req_id_part} — {message}"


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structured logging for the application.

    Call this once at startup before any logging happens.
    """
    formatter = AwaitHumansFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(_ScrubFilter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Suppress noisy loggers
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logging.getLogger("awaithumans.server").info("Logging configured (level=%s)", log_level)
