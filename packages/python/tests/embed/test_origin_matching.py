"""Tests for origin allowlist parsing and matching (§4.3).

Covers:
  1. Strip whitespace and drop empty entries in parse_origin_allowlist.
  2. Reject entries with a path component.
  3. Reject entries with trailing slash.
  4. Reject entries with multiple wildcards (double-wildcard).
  5. Reject http for non-localhost hosts.
  6. Accept http for localhost.
  7. Accept http for 127.0.0.1.
  8. Exact origin match.
  9. Wildcard matches one DNS label below apex (app.acme.com).
  10. Wildcard does NOT match apex itself (acme.com).
  11. Wildcard does NOT match two labels below apex (a.b.acme.com).
  12. Scheme must match exactly (http vs https).
  13. Port must match exactly (:8443).
"""

from __future__ import annotations

import pytest

from awaithumans.server.services.embed_token_service import (
    InvalidAllowlistEntryError,
    origin_in_allowlist,
    parse_origin_allowlist,
)

# ── 1. Whitespace stripping and empty entry dropping ──────────────────────


def test_parse_strips_whitespace_and_drops_empty_entries() -> None:
    """Whitespace around entries is stripped; blank entries (trailing comma etc.) are skipped."""
    result = parse_origin_allowlist("  https://app.acme.com  ,  , https://staging.acme.com  ")
    assert result == ("https://app.acme.com", "https://staging.acme.com")


def test_parse_empty_string_returns_empty_tuple() -> None:
    """Empty raw string produces an empty tuple, not an error."""
    assert parse_origin_allowlist("") == ()


def test_parse_whitespace_only_returns_empty_tuple() -> None:
    """Whitespace-only raw string (e.g. '   ') produces an empty tuple."""
    assert parse_origin_allowlist("   ,  ,  ") == ()


# ── 2. Reject path component ──────────────────────────────────────────────


def test_reject_entry_with_path() -> None:
    """An entry with a path component (https://acme.com/login) is invalid."""
    with pytest.raises(InvalidAllowlistEntryError):
        parse_origin_allowlist("https://acme.com/login")


# ── 3. Reject trailing slash ──────────────────────────────────────────────


def test_reject_entry_with_trailing_slash() -> None:
    """An entry with a trailing slash (https://acme.com/) is invalid."""
    with pytest.raises(InvalidAllowlistEntryError):
        parse_origin_allowlist("https://acme.com/")


# ── 4. Reject double-wildcard ─────────────────────────────────────────────


def test_reject_double_wildcard_entry() -> None:
    """An entry with multiple wildcards (https://*.*.acme.com) is rejected."""
    with pytest.raises(InvalidAllowlistEntryError):
        parse_origin_allowlist("https://*.*.acme.com")


# ── 5. Reject http for non-localhost ──────────────────────────────────────


def test_reject_http_for_non_localhost() -> None:
    """http is not allowed for non-localhost origins (e.g. http://acme.com)."""
    with pytest.raises(InvalidAllowlistEntryError):
        parse_origin_allowlist("http://acme.com")


def test_reject_http_wildcard_for_non_localhost() -> None:
    """http is not allowed for wildcard non-localhost entries."""
    with pytest.raises(InvalidAllowlistEntryError):
        parse_origin_allowlist("http://*.acme.com")


# ── 6. Accept http for localhost ──────────────────────────────────────────


def test_accept_http_for_localhost() -> None:
    """http://localhost is a valid allowlist entry."""
    result = parse_origin_allowlist("http://localhost")
    assert result == ("http://localhost",)


def test_accept_http_for_localhost_with_port() -> None:
    """http://localhost:3000 is a valid allowlist entry."""
    result = parse_origin_allowlist("http://localhost:3000")
    assert result == ("http://localhost:3000",)


# ── 7. Accept http for 127.0.0.1 ─────────────────────────────────────────


def test_accept_http_for_loopback_ip() -> None:
    """http://127.0.0.1 is a valid allowlist entry."""
    result = parse_origin_allowlist("http://127.0.0.1")
    assert result == ("http://127.0.0.1",)


def test_accept_http_for_loopback_ip_with_port() -> None:
    """http://127.0.0.1:8080 is a valid allowlist entry."""
    result = parse_origin_allowlist("http://127.0.0.1:8080")
    assert result == ("http://127.0.0.1:8080",)


# ── 8. Exact match ────────────────────────────────────────────────────────


def test_exact_match_returns_true() -> None:
    """An origin exactly equal to an allowlist entry matches."""
    allowlist = parse_origin_allowlist("https://app.acme.com")
    assert origin_in_allowlist("https://app.acme.com", allowlist) is True


def test_exact_match_different_origin_returns_false() -> None:
    """An origin not in the allowlist does not match."""
    allowlist = parse_origin_allowlist("https://app.acme.com")
    assert origin_in_allowlist("https://other.acme.com", allowlist) is False


# ── 9. Wildcard matches one label below apex ──────────────────────────────


def test_wildcard_matches_one_label_below_apex() -> None:
    """*.acme.com matches app.acme.com."""
    allowlist = parse_origin_allowlist("https://*.acme.com")
    assert origin_in_allowlist("https://app.acme.com", allowlist) is True


def test_wildcard_matches_staging_label() -> None:
    """*.acme.com matches staging.acme.com."""
    allowlist = parse_origin_allowlist("https://*.acme.com")
    assert origin_in_allowlist("https://staging.acme.com", allowlist) is True


# ── 10. Wildcard does NOT match apex ──────────────────────────────────────


def test_wildcard_does_not_match_apex() -> None:
    """*.acme.com must NOT match acme.com itself."""
    allowlist = parse_origin_allowlist("https://*.acme.com")
    assert origin_in_allowlist("https://acme.com", allowlist) is False


# ── 11. Wildcard does NOT match two labels below apex ────────────────────


def test_wildcard_does_not_match_two_labels_below_apex() -> None:
    """*.acme.com must NOT match a.b.acme.com (two labels below apex)."""
    allowlist = parse_origin_allowlist("https://*.acme.com")
    assert origin_in_allowlist("https://a.b.acme.com", allowlist) is False


# ── 12. Scheme must match ─────────────────────────────────────────────────


def test_scheme_mismatch_returns_false() -> None:
    """An https allowlist entry must not match an http origin."""
    allowlist = parse_origin_allowlist("https://app.acme.com")
    assert origin_in_allowlist("http://app.acme.com", allowlist) is False


def test_http_localhost_does_not_match_https_origin() -> None:
    """http://localhost in allowlist must not match https://localhost."""
    allowlist = parse_origin_allowlist("http://localhost")
    assert origin_in_allowlist("https://localhost", allowlist) is False


# ── 13. Port must match ───────────────────────────────────────────────────


def test_port_mismatch_returns_false() -> None:
    """An allowlist entry with :8443 must not match origin without explicit port."""
    allowlist = parse_origin_allowlist("https://app.acme.com:8443")
    assert origin_in_allowlist("https://app.acme.com", allowlist) is False


def test_explicit_port_matches_same_port() -> None:
    """An allowlist entry with :8443 matches an origin with :8443."""
    allowlist = parse_origin_allowlist("https://app.acme.com:8443")
    assert origin_in_allowlist("https://app.acme.com:8443", allowlist) is True


def test_default_port_443_matches_no_explicit_port() -> None:
    """https://app.acme.com:443 in allowlist must match https://app.acme.com (default port 443)."""
    allowlist = parse_origin_allowlist("https://app.acme.com:443")
    assert origin_in_allowlist("https://app.acme.com", allowlist) is True


def test_default_port_80_matches_no_explicit_port() -> None:
    """http://localhost:80 in allowlist must match http://localhost (default port 80)."""
    allowlist = parse_origin_allowlist("http://localhost:80")
    assert origin_in_allowlist("http://localhost", allowlist) is True
