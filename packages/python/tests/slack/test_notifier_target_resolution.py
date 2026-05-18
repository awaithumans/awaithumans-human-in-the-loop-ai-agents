"""Slack notifier target resolution — `slack:@alice` works.

The user-facing contract: `notify=["slack:@alice"]` (the Slack handle)
is equivalent to `notify=["slack:@U_ALICE_ID"]` (the user_id). The
resolver translates handles to user_ids via `users.list` because
Slack's `chat.postMessage` doesn't do that translation itself —
posting to `@alice` raw silently fails.

Three resolution paths covered:

  - User ID → pass through (`@U123ABC` and `U123ABC`)
  - Channel sigil → pass through (`#general`, `C123ABC`)
  - Handle → resolved via `users.list` (`@alice` → `U_ALICE`)
  - Email → resolved via `users.lookupByEmail` (`alice@acme.com` → `U_ALICE`)

Cache hits don't re-call the API. Cache misses log + return None.
"""

from __future__ import annotations

import pytest

from awaithumans.server.channels.slack.resolution import (
    _HANDLE_CACHE,
)
from awaithumans.server.channels.slack.resolution import (
    resolve_slack_target as _resolve_target,
)

# ─── Fakes ───────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, data: dict) -> None:
        self._data = data

    def get(self, key: str, default=None):
        return self._data.get(key, default)


class _FakeClient:
    """Stand-in for `slack_sdk.web.async_client.AsyncWebClient`.

    Records every method call so tests assert on (method, args). The
    `users.list` payload is fully scriptable so each test can pin a
    different roster."""

    def __init__(self, members: list[dict] | None = None) -> None:
        self._members = members or []
        self.calls: list[str] = []
        self._lookup_by_email_response: dict | None = None
        self._users_list_error: Exception | None = None

    def with_email_lookup(self, email: str, user_id: str) -> _FakeClient:
        self._lookup_by_email_response = {
            "expected_email": email,
            "user_id": user_id,
        }
        return self

    def with_users_list_error(self, exc: Exception) -> _FakeClient:
        self._users_list_error = exc
        return self

    async def users_list(self) -> _FakeResp:
        self.calls.append("users_list")
        if self._users_list_error is not None:
            raise self._users_list_error
        return _FakeResp({"members": self._members})

    # Slack SDK uses camelCase here; matching that exactly so this fake
    # is drop-in for `slack_sdk.web.async_client.AsyncWebClient`.
    async def users_lookupByEmail(self, *, email: str) -> _FakeResp:  # noqa: N802
        self.calls.append(f"users_lookupByEmail:{email}")
        if self._lookup_by_email_response is None:
            return _FakeResp({"user": {}})
        if email != self._lookup_by_email_response["expected_email"]:
            return _FakeResp({"user": {}})
        return _FakeResp({"user": {"id": self._lookup_by_email_response["user_id"]}})


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty handle cache so prior tests
    can't poison the lookup."""
    _HANDLE_CACHE.clear()
    yield
    _HANDLE_CACHE.clear()


# ─── Pass-through paths ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_channel_sigil_passes_through() -> None:
    client = _FakeClient()
    out = await _resolve_target(client=client, target="#general", team_id=None)
    assert out == "#general"
    # No API call needed.
    assert client.calls == []


@pytest.mark.asyncio
async def test_user_id_with_at_sign_strips_and_returns() -> None:
    """`@U_ALICE` → `U_ALICE` — Slack's chat.postMessage doesn't
    want the leading `@`."""
    client = _FakeClient()
    out = await _resolve_target(client=client, target="@U01ABC234", team_id=None)
    assert out == "U01ABC234"
    assert client.calls == []


@pytest.mark.asyncio
async def test_user_id_without_at_sign_passes_through() -> None:
    client = _FakeClient()
    out = await _resolve_target(client=client, target="U01ABC234", team_id=None)
    assert out == "U01ABC234"
    assert client.calls == []


@pytest.mark.asyncio
async def test_channel_id_passes_through() -> None:
    client = _FakeClient()
    out = await _resolve_target(client=client, target="C01ABC234", team_id=None)
    assert out == "C01ABC234"
    assert client.calls == []


# ─── Handle resolution via users.list ────────────────────────────────


@pytest.mark.asyncio
async def test_handle_resolves_via_users_list() -> None:
    client = _FakeClient(
        members=[
            {
                "id": "U_ALICE",
                "name": "alice",
                "real_name": "Alice Singh",
                "is_bot": False,
                "deleted": False,
                "profile": {"display_name": "alice.s", "real_name": "Alice Singh"},
            },
            {
                "id": "U_BOB",
                "name": "bob",
                "is_bot": False,
                "deleted": False,
                "profile": {"display_name": "", "real_name": "Bob"},
            },
        ]
    )

    assert await _resolve_target(client=client, target="@alice", team_id=None) == "U_ALICE"
    assert await _resolve_target(client=client, target="@bob", team_id=None) == "U_BOB"


@pytest.mark.asyncio
async def test_handle_resolution_is_case_insensitive() -> None:
    client = _FakeClient(
        members=[
            {"id": "U_ALICE", "name": "alice", "is_bot": False, "deleted": False, "profile": {}}
        ]
    )
    assert await _resolve_target(client=client, target="@Alice", team_id=None) == "U_ALICE"
    assert await _resolve_target(client=client, target="@ALICE", team_id=None) == "U_ALICE"


@pytest.mark.asyncio
async def test_handle_can_match_display_name_or_real_name() -> None:
    """If the operator types `@alice.singh` (display_name) or
    `@Alice Singh` (real_name), still resolves."""
    client = _FakeClient(
        members=[
            {
                "id": "U_ALICE",
                "name": "alice",
                "is_bot": False,
                "deleted": False,
                "profile": {
                    "display_name": "alice.singh",
                    "real_name": "Alice Singh",
                },
            }
        ]
    )
    assert await _resolve_target(client=client, target="@alice.singh", team_id=None) == "U_ALICE"
    # real_name with a space — operators rarely do this but it works.
    assert await _resolve_target(client=client, target="@Alice Singh", team_id=None) == "U_ALICE"


@pytest.mark.asyncio
async def test_handle_returns_none_when_not_found() -> None:
    client = _FakeClient(
        members=[
            {"id": "U_ALICE", "name": "alice", "is_bot": False, "deleted": False, "profile": {}}
        ]
    )
    assert await _resolve_target(client=client, target="@nonexistent", team_id=None) is None


@pytest.mark.asyncio
async def test_bots_and_deleted_users_excluded() -> None:
    """Bots / Slackbot / deleted accounts must NOT be resolution
    targets — sending to them would either fail or annoy the wrong
    person."""
    client = _FakeClient(
        members=[
            {
                "id": "USLACKBOT",
                "name": "slackbot",
                "is_bot": True,
                "deleted": False,
                "profile": {},
            },
            {"id": "U_BOT", "name": "mybot", "is_bot": True, "deleted": False, "profile": {}},
            {"id": "U_GHOST", "name": "ghost", "is_bot": False, "deleted": True, "profile": {}},
        ]
    )
    assert await _resolve_target(client=client, target="@slackbot", team_id=None) is None
    assert await _resolve_target(client=client, target="@mybot", team_id=None) is None
    assert await _resolve_target(client=client, target="@ghost", team_id=None) is None


# ─── Cache behaviour ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_users_list_called_once_then_cache_serves() -> None:
    """High-traffic queues shouldn't hammer `users.list` — once per
    process is the contract."""
    client = _FakeClient(
        members=[
            {"id": "U_ALICE", "name": "alice", "is_bot": False, "deleted": False, "profile": {}}
        ]
    )

    await _resolve_target(client=client, target="@alice", team_id=None)
    await _resolve_target(client=client, target="@alice", team_id=None)
    await _resolve_target(client=client, target="@alice", team_id=None)

    # Only one users.list call across three resolutions.
    assert client.calls.count("users_list") == 1


@pytest.mark.asyncio
async def test_cache_keyed_by_team() -> None:
    """Two distinct teams → two distinct caches."""
    client_a = _FakeClient(
        members=[{"id": "U_A", "name": "alice", "is_bot": False, "deleted": False, "profile": {}}]
    )
    client_b = _FakeClient(
        members=[{"id": "U_B", "name": "alice", "is_bot": False, "deleted": False, "profile": {}}]
    )

    out_a = await _resolve_target(client=client_a, target="@alice", team_id="T_AAA")
    out_b = await _resolve_target(client=client_b, target="@alice", team_id="T_BBB")
    assert out_a == "U_A"
    assert out_b == "U_B"


# ─── Email resolution ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_email_resolves_via_lookup_by_email() -> None:
    """Email-shaped targets use the dedicated lookup endpoint, not
    users.list — cheaper + has its own rate limit."""
    client = _FakeClient().with_email_lookup("alice@acme.com", "U_ALICE")

    out = await _resolve_target(client=client, target="alice@acme.com", team_id=None)
    assert out == "U_ALICE"
    assert "users_lookupByEmail:alice@acme.com" in client.calls
    # Did NOT fall through to users.list.
    assert "users_list" not in client.calls


@pytest.mark.asyncio
async def test_unknown_email_returns_none() -> None:
    client = _FakeClient()  # no email lookup configured
    out = await _resolve_target(client=client, target="ghost@acme.com", team_id=None)
    assert out is None
