"""Email transport backends — EmailMessage validation + per-backend send."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from awaithumans.server.channels.email.transport import (
    EmailMessage,
    EmailTransportError,
    resolve_transport,
)
from awaithumans.server.channels.email.transport.logging import LoggingTransport
from awaithumans.server.channels.email.transport.noop import NoopTransport
from awaithumans.server.channels.email.transport.resend import ResendTransport

# ─── EmailMessage header-injection defense ──────────────────────────────


def test_message_formatted_from_no_name() -> None:
    m = EmailMessage(
        to="a@x.com",
        subject="s",
        html="<p/>",
        text="t",
        from_email="f@x.com",
    )
    assert m.formatted_from() == "f@x.com"


def test_message_formatted_from_with_name() -> None:
    m = EmailMessage(
        to="a@x.com",
        subject="s",
        html="<p/>",
        text="t",
        from_email="f@x.com",
        from_name="Acme",
    )
    assert m.formatted_from() == '"Acme" <f@x.com>'


@pytest.mark.parametrize(
    "kwarg,value",
    [
        ("to", "a@x.com\r\nBcc: attacker@evil.com"),
        ("subject", "hello\r\nX-Header: injected"),
        ("from_email", "f@x.com\nBcc: bad@x.com"),
        ("from_name", "Name\r\nInjected-Header: x"),
        ("reply_to", "a@x.com\nInjected: x"),
    ],
)
def test_crlf_in_any_header_rejected(kwarg: str, value: str) -> None:
    base = dict(
        to="a@x.com",
        subject="s",
        html="<p/>",
        text="t",
        from_email="f@x.com",
    )
    base[kwarg] = value
    with pytest.raises(ValueError, match="header injection"):
        EmailMessage(**base)


# ─── Factory ────────────────────────────────────────────────────────────


def test_factory_unknown_transport_raises() -> None:
    with pytest.raises(EmailTransportError, match="Unknown email transport"):
        resolve_transport("bogus", {})


def test_factory_builds_noop() -> None:
    assert resolve_transport("noop", {}).name == "noop"


def test_factory_builds_logging() -> None:
    assert resolve_transport("logging", {}).name == "logging"


def test_factory_resend_requires_api_key() -> None:
    with pytest.raises(EmailTransportError, match="api_key"):
        resolve_transport("resend", {})
    t = resolve_transport("resend", {"api_key": "re_test"})
    assert t.name == "resend"


def test_factory_smtp_requires_host() -> None:
    with pytest.raises(EmailTransportError, match="host"):
        resolve_transport("smtp", {})
    t = resolve_transport("smtp", {"host": "smtp.example.com", "port": 587})
    assert t.name == "smtp"


def test_factory_smtp_accepts_user_alias_for_username() -> None:
    """The dashboard form hint advertises `user`, Python stdlib smtplib
    uses `user` too — the factory must accept either spelling. Before
    this fix, the `user` key was silently dropped, leaving operators
    with unauthenticated SMTP and no signal."""
    t = resolve_transport(
        "smtp",
        {
            "host": "smtp.example.com",
            "port": 587,
            "user": "alice@example.com",
            "password": "secret",
        },
    )
    assert t._username == "alice@example.com"  # type: ignore[attr-defined]
    assert t._password == "secret"  # type: ignore[attr-defined]


def test_factory_smtp_username_wins_over_user_when_both_present() -> None:
    """Explicit `username` takes precedence over the `user` alias —
    avoids ambiguity if a config carries both keys."""
    t = resolve_transport(
        "smtp",
        {
            "host": "smtp.example.com",
            "port": 587,
            "username": "canonical@x.com",
            "user": "legacy@x.com",
        },
    )
    assert t._username == "canonical@x.com"  # type: ignore[attr-defined]


def test_factory_smtp_port_465_defaults_use_tls_true() -> None:
    """Port 465 is implicit-TLS by convention. The factory must default
    use_tls=True there — STARTTLS on 465 fails the handshake, which is
    the exact trap most operators (Hostinger, Zoho, fastmail, etc.) hit
    on the first send."""
    t = resolve_transport("smtp", {"host": "smtp.example.com", "port": 465})
    assert t._use_tls is True  # type: ignore[attr-defined]
    # SMTPTransport's __init__ resolves the use_tls + start_tls
    # conflict by clearing start_tls when use_tls is True.
    assert t._start_tls is False  # type: ignore[attr-defined]


def test_factory_smtp_port_587_defaults_use_tls_false() -> None:
    """Port 587 is STARTTLS by convention; use_tls stays False so the
    handshake upgrades after EHLO. No regression for existing setups."""
    t = resolve_transport("smtp", {"host": "smtp.example.com", "port": 587})
    assert t._use_tls is False  # type: ignore[attr-defined]
    assert t._start_tls is True  # type: ignore[attr-defined]


def test_factory_smtp_explicit_use_tls_overrides_port_default() -> None:
    """Explicit use_tls=False on port 465 is respected — operator knows
    something we don't (e.g. a non-standard relay)."""
    t = resolve_transport(
        "smtp",
        {"host": "smtp.example.com", "port": 465, "use_tls": False},
    )
    assert t._use_tls is False  # type: ignore[attr-defined]


# ─── Noop + Logging (trivial, smoke tests) ──────────────────────────────


@pytest.mark.asyncio
async def test_noop_send_returns_id() -> None:
    t = NoopTransport()
    msg = EmailMessage(to="a@x.com", subject="s", html="<p/>", text="t", from_email="f@x.com")
    result = await t.send(msg)
    assert result.transport == "noop"
    assert result.message_id and result.message_id.startswith("noop-")


@pytest.mark.asyncio
async def test_logging_send_returns_id() -> None:
    t = LoggingTransport()
    msg = EmailMessage(to="a@x.com", subject="s", html="<p/>", text="t", from_email="f@x.com")
    result = await t.send(msg)
    assert result.transport == "logging"


# ─── Resend — mocked HTTPS ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resend_send_success() -> None:
    t = ResendTransport(api_key="re_test")
    msg = EmailMessage(to="a@x.com", subject="s", html="<p/>", text="t", from_email="f@x.com")

    class FakeResp:
        status_code = 200
        content = b'{"id": "msg_123"}'

        def json(self) -> dict[str, str]:
            return {"id": "msg_123"}

    fake_post = AsyncMock(return_value=FakeResp())
    with patch("httpx.AsyncClient.post", fake_post):
        result = await t.send(msg)

    assert result.transport == "resend"
    assert result.message_id == "msg_123"
    # Verify auth header + JSON body went out.
    assert fake_post.await_count == 1
    call_kwargs = fake_post.await_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Bearer re_test"
    body = call_kwargs["json"]
    assert body["to"] == ["a@x.com"]
    assert body["from"] == "f@x.com"


@pytest.mark.asyncio
async def test_resend_send_failure_raises() -> None:
    t = ResendTransport(api_key="re_test")
    msg = EmailMessage(to="a@x.com", subject="s", html="<p/>", text="t", from_email="f@x.com")

    class FakeResp:
        status_code = 401
        text = '{"error": "unauthorized"}'
        content = b'{"error":"unauthorized"}'

        def json(self) -> dict:
            return {"error": "unauthorized"}

    fake_post = AsyncMock(return_value=FakeResp())
    with (
        patch("httpx.AsyncClient.post", fake_post),
        pytest.raises(EmailTransportError, match="HTTP 401"),
    ):
        await t.send(msg)


def test_resend_requires_api_key_construction() -> None:
    with pytest.raises(EmailTransportError):
        ResendTransport(api_key="")
