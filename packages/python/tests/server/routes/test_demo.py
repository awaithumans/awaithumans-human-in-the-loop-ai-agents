"""HTTP route tests for ``/api/demo/start`` and ``/api/demo/{id}/status``.

These exercise the wire shape (multipart parsing, IP-hash derivation,
public-prefix bypass on dashboard auth). The Turnstile + extractor
network calls are stubbed at the orchestrator boundary so the suite
stays hermetic.
"""

from __future__ import annotations

import io
from unittest.mock import patch

from fastapi.testclient import TestClient

from awaithumans.server.services.demo.extractor import ExtractionResult


def test_start_demo_happy(client: TestClient) -> None:
    """Multipart POST returns the extractor result and the list of
    flagged fields the wizard should leave as "awaiting reviewer."
    """
    with (
        patch(
            "awaithumans.server.services.demo.service.verify_turnstile_token",
            return_value=None,
        ),
        patch("awaithumans.server.services.demo.service.run_demo_extraction") as mock_run,
    ):
        mock_run.return_value = ExtractionResult(
            values={"vendor": "Acme", "total_cents": 1299, "date": "2026-01-01"},
            confidences={"vendor": 0.97, "total_cents": 0.62, "date": 0.95},
            cost_cents=6,
        )
        response = client.post(
            "/api/demo/start",
            data={
                "email": "alice@acme.com",
                "preset_key": "Receipt",
                "is_hot_demo": "false",
                "turnstileToken": "t",
            },
            files={
                "page_image": (
                    "page.png",
                    io.BytesIO(b"\x89PNG fake"),
                    "image/png",
                )
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ai_result"]["vendor"] == "Acme"
    assert "demo_id" in body
    assert body["pending_field_names"] == ["total_cents"]


def test_start_demo_rejects_gmail(client: TestClient) -> None:
    """Gmail and other free-mail domains fail the email gate with a
    400. The reviewer pool isn't going to spend cycles on personal
    addresses."""
    with patch(
        "awaithumans.server.services.demo.service.verify_turnstile_token",
        return_value=None,
    ):
        response = client.post(
            "/api/demo/start",
            data={
                "email": "alice@gmail.com",
                "preset_key": "Receipt",
                "is_hot_demo": "false",
                "turnstileToken": "t",
            },
            files={
                "page_image": (
                    "page.png",
                    io.BytesIO(b"\x89PNG"),
                    "image/png",
                )
            },
        )
    assert response.status_code == 400, response.text


def test_status_route_returns_404(client: TestClient) -> None:
    """Unknown demo_id surfaces as 404 via DemoRecordNotFoundError →
    the centralized service-exception handler."""
    response = client.get("/api/demo/does-not-exist/status")
    assert response.status_code == 404, response.text


def test_submit_field_route_requires_auth(client: TestClient) -> None:
    """Anonymous callers can't submit field corrections.

    The route does its own session check because ``/api/demo/`` is in
    the public-prefix allowlist (the visitor's wizard polls
    ``/status`` without a cookie). Without a session cookie OR admin
    bearer, the route must 401 before any DB read so we don't leak
    which demo IDs exist.
    """
    response = client.post(
        "/api/demo/does-not-exist/field/total/submit",
        json={"value": 1234},
    )
    assert response.status_code in (401, 404), response.text
    # We expect 401 in v2: auth runs before the 404 lookup so that
    # demo IDs aren't enumerable. Anything else is a regression.
    assert response.status_code == 401
