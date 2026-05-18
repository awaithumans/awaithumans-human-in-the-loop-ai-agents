"""CORS origin + PUBLIC_URL validation at boot.

The middleware in `app.py` flips `allow_credentials` ON the moment the
origin list isn't a bare `*`. Without validation, any operator who
sets a malformed origin list re-enables credential-bearing CORS to
whatever they typed — classic session-ride trap.

These tests pin the validator: starless `*`, plain http://, mixed
configs all refuse to start with an actionable error. The acceptable
shapes (bare `*`, https-only, http-localhost dev) are also pinned so
we don't regress them away."""

from __future__ import annotations

import pytest

from awaithumans.server.app import _validate_cors_origins, _validate_public_url

# ─── Acceptable configs ──────────────────────────────────────────────


def test_bare_wildcard_accepted() -> None:
    """`["*"]` alone is fine — credentials are forced off in this
    case so any origin can read public data without session cookies."""
    _validate_cors_origins(["*"])  # no raise


def test_https_origin_accepted() -> None:
    _validate_cors_origins(["https://app.acme.com"])


def test_https_origin_with_port_accepted() -> None:
    _validate_cors_origins(["https://app.acme.com:8443"])


def test_localhost_dev_accepted() -> None:
    _validate_cors_origins(["http://localhost:3000"])


def test_loopback_dev_accepted() -> None:
    _validate_cors_origins(["http://127.0.0.1:5173"])


def test_multiple_https_origins_accepted() -> None:
    _validate_cors_origins(["https://app.acme.com", "https://admin.acme.com:443"])


# ─── Refused configs ─────────────────────────────────────────────────


def test_plain_http_non_local_refused() -> None:
    """http:// to a non-localhost destination would carry session
    cookies in cleartext."""
    with pytest.raises(RuntimeError, match="unsafe origin"):
        _validate_cors_origins(["http://app.acme.com"])


def test_wildcard_mixed_with_explicit_refused() -> None:
    """Browsers reject this combination, but our middleware would
    flip credentials on regardless. Block at boot."""
    with pytest.raises(RuntimeError, match="alongside explicit"):
        _validate_cors_origins(["*", "https://app.acme.com"])


def test_garbage_origin_refused() -> None:
    """A typo / stray space doesn't silently widen the policy."""
    with pytest.raises(RuntimeError):
        _validate_cors_origins(["app.acme.com"])  # missing scheme


def test_origin_with_path_refused() -> None:
    """CORS origins must be scheme + host + optional port — no path."""
    with pytest.raises(RuntimeError):
        _validate_cors_origins(["https://app.acme.com/api"])


# ─── PUBLIC_URL validation ───────────────────────────────────────────


class TestPublicUrlValidation:
    """`PUBLIC_URL` must be scheme + host + optional port — no path.

    Operators commonly paste the full Slack OAuth callback URL into
    this env var, which then breaks every constructed URL (dashboard
    click-throughs become `/api/channels/slack/oauth/callback/task?id=…`).
    Fail at boot with a clear fix instead of letting them debug 404s."""

    def test_localhost_dev_accepted(self) -> None:
        _validate_public_url("http://localhost:3001")
        _validate_public_url("http://localhost")

    def test_loopback_accepted(self) -> None:
        _validate_public_url("http://127.0.0.1:3001")

    def test_https_host_accepted(self) -> None:
        _validate_public_url("https://reviews.acme.com")
        _validate_public_url("https://reviews.acme.com:8443")

    def test_ngrok_subdomain_accepted(self) -> None:
        _validate_public_url("https://abcd1234.ngrok-free.app")

    def test_trailing_slash_tolerated(self) -> None:
        _validate_public_url("https://reviews.acme.com/")

    def test_full_oauth_callback_url_refused(self) -> None:
        """The classic operator footgun: pasting the OAuth redirect URL
        instead of the base. Server constructs every URL by joining
        PUBLIC_URL + path, so this would yield `…/api/channels/slack/
        oauth/callback/task?id=…` for the dashboard link."""
        with pytest.raises(RuntimeError, match="not a base URL"):
            _validate_public_url("https://example.com/api/channels/slack/oauth/callback")

    def test_path_segment_refused(self) -> None:
        with pytest.raises(RuntimeError, match="not a base URL"):
            _validate_public_url("https://example.com/something")

    def test_empty_refused(self) -> None:
        with pytest.raises(RuntimeError, match="is unset"):
            _validate_public_url("")

    def test_missing_scheme_refused(self) -> None:
        with pytest.raises(RuntimeError, match="not a base URL"):
            _validate_public_url("reviews.acme.com")
