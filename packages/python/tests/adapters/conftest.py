"""Shared fixtures for adapter tests.

The temporal/langgraph adapters and the webhook-signing path that
backs them read `AWAITHUMANS_PAYLOAD_KEY` from `os.environ` directly
(see PR #71 — the webhook_signing module stays free of the
pydantic-settings dependency so it works in adapter receivers that
don't install [server]).

Without this fixture, every test that exercises a signed callback
fails at module-load with `PayloadKeyMissingError`. Mirrors the
identical fixture in `tests/auth/conftest.py`.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _payload_key_for_adapters() -> Iterator[None]:
    fresh = secrets.token_urlsafe(32)
    original_env = os.environ.get("AWAITHUMANS_PAYLOAD_KEY")
    os.environ["AWAITHUMANS_PAYLOAD_KEY"] = fresh

    # Reset the webhook-signing module's cached key so it picks up
    # `fresh` on the next derivation.
    from awaithumans.utils import webhook_signing

    webhook_signing.reset_cache()

    yield

    if original_env is None:
        os.environ.pop("AWAITHUMANS_PAYLOAD_KEY", None)
    else:
        os.environ["AWAITHUMANS_PAYLOAD_KEY"] = original_env
    webhook_signing.reset_cache()
