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


def test_rejects_trailing_newline_on_free_domain() -> None:
    # Regression: `re.match` with `$` would let a trailing newline bypass
    # the deny list because `$` matches before `\n`. The gate now strips
    # and uses `\Z`, so this MUST still raise.
    with pytest.raises(InvalidDemoEmailError):
        validate_demo_email("alice@gmail.com\n", allowlist_extra=frozenset())


def test_accepts_surrounding_whitespace() -> None:
    # Paste-friendly: leading/trailing whitespace is stripped before the
    # check so a pasted address with a stray space is not falsely
    # rejected as malformed.
    validate_demo_email("  alice@acme.com  ", allowlist_extra=frozenset())


def test_rejects_inner_whitespace() -> None:
    with pytest.raises(InvalidDemoEmailError):
        validate_demo_email("alice @acme.com", allowlist_extra=frozenset())
