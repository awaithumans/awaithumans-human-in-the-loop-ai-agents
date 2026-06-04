"""Demo-specific service exceptions.

All inherit from ServiceError so the centralized exception handler in
core/exceptions.py builds the HTTP response without per-exception
handlers. Copy is intentionally generic for rate-limit/capacity hits so
we don't leak which gate tripped (anti-enumeration).
"""

from __future__ import annotations

from awaithumans.server.services.exceptions import ServiceError


class DemoError(ServiceError):
    """Base for demo errors."""

    docs_path = "demo"


class InvalidDemoEmailError(DemoError):
    status_code = 400
    error_code = "DEMO_INVALID_EMAIL"
    docs_path = "demo-work-email-required"

    def __init__(self) -> None:
        super().__init__(
            "Use your work email — Gmail and other personal addresses aren't accepted."
        )


class DemoPerEmailCapError(DemoError):
    status_code = 429
    error_code = "DEMO_EMAIL_CAP"
    docs_path = "demo-rate-limit"

    def __init__(self) -> None:
        super().__init__(
            "You've already tried the demo this week. "
            "Sign up for the real thing — $5 free credit applied automatically."
        )


class DemoCapacityError(DemoError):
    """Per-IP, global daily, or cost ceiling. Same copy for all so the
    response doesn't leak which gate tripped."""

    status_code = 429
    error_code = "DEMO_CAPACITY"
    docs_path = "demo-rate-limit"

    def __init__(self) -> None:
        super().__init__(
            "Demo's at capacity right now. Sign up to skip the line — $5 free credit on signup."
        )


class TurnstileVerificationError(DemoError):
    status_code = 403
    error_code = "DEMO_TURNSTILE_FAIL"
    docs_path = "demo-turnstile"

    def __init__(self) -> None:
        super().__init__("Couldn't verify you're human. Try again.")


class DemoSchemaError(DemoError):
    status_code = 400
    error_code = "DEMO_SCHEMA_INVALID"
    docs_path = "demo-schema-invalid"


class DemoProviderError(DemoError):
    """Sanitized for the client. Real provider error logged server-side."""

    status_code = 502
    error_code = "DEMO_PROVIDER_FAIL"
    docs_path = "demo-provider-fail"

    def __init__(self, provider_label: str) -> None:
        super().__init__(f"{provider_label} wouldn't process this file. Try a different provider.")


class DemoRecordNotFoundError(DemoError):
    status_code = 404
    error_code = "DEMO_NOT_FOUND"
    docs_path = "demo-not-found"

    def __init__(self, demo_id: str) -> None:
        super().__init__(f"Demo {demo_id} not found.")
        self.demo_id = demo_id
