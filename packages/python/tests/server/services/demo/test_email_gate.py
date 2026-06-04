from __future__ import annotations

import pytest

from awaithumans.server.services.demo.email_gate import validate_demo_email
from awaithumans.server.services.demo.exceptions import InvalidDemoEmailError


def test_accepts_work_domain() -> None:
    validate_demo_email("alice@acme.com", allowlist_extra=frozenset())


def test_rejects_gmail() -> None:
    with pytest.raises(InvalidDemoEmailError):
        validate_demo_email("alice@gmail.com", allowlist_extra=frozenset())


def test_rejects_disposable() -> None:
    with pytest.raises(InvalidDemoEmailError):
        validate_demo_email("alice@mailinator.com", allowlist_extra=frozenset())


def test_rejects_malformed() -> None:
    with pytest.raises(InvalidDemoEmailError):
        validate_demo_email("not-an-email", allowlist_extra=frozenset())


def test_rejects_missing_domain() -> None:
    with pytest.raises(InvalidDemoEmailError):
        validate_demo_email("alice@", allowlist_extra=frozenset())


def test_allowlist_override() -> None:
    validate_demo_email(
        "alice@gmail.com",
        allowlist_extra=frozenset({"gmail.com"}),
    )


def test_case_insensitive_domain() -> None:
    with pytest.raises(InvalidDemoEmailError):
        validate_demo_email("Alice@Gmail.COM", allowlist_extra=frozenset())
