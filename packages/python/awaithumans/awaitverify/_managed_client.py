"""HTTP client for the AwaitVerify managed backend.

Three calls in v1:
  - POST /api/v1/awaitverify/uploads      → mint signed URLs + DEK
  - POST /api/v1/awaitverify/tasks        → create task
  - GET  /api/v1/awaitverify/tasks/{id}/poll → wait for terminal state

All calls authenticate with the customer's AwaitHumans API key via
`Authorization: Bearer <key>`. Errors are surfaced as typed
exceptions so callers can branch cleanly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from awaithumans.awaitverify.errors import VerifyError

logger = logging.getLogger("awaithumans.awaitverify.managed_client")

_DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class FragmentSlot:
    page_index: int
    fragment_index: int
    key: str
    upload_url: str
    upload_headers: dict[str, str]
    expires_at_unix: int


@dataclass(frozen=True)
class UploadSession:
    upload_session_id: str
    dek: bytes  # plaintext DEK, kept in caller memory only
    fragments: list[FragmentSlot]
    expires_in_seconds: int


@dataclass(frozen=True)
class CreatedTask:
    task_id: str
    upload_session_id: str
    status: str


@dataclass(frozen=True)
class PolledTask:
    task_id: str
    status: str
    response_json: str | None


class ManagedBackendError(VerifyError):
    """Non-2xx response from the AwaitVerify managed backend."""

    def __init__(self, *, status_code: int, body: str, endpoint: str) -> None:
        super().__init__(
            code="MANAGED_BACKEND_ERROR",
            message=(f"AwaitVerify managed backend returned {status_code} on {endpoint}."),
            hint=(
                f"Response body: {body[:500]}\n\n"
                "  - 401/403: check the api_key on your AwaitHumans client.\n"
                "  - 404: the upload session or task id was not recognized.\n"
                "  - 422: a request field failed validation — check the response body.\n"
                "  - 5xx: transient — retry or check status.awaithumans.dev."
            ),
            docs_url="https://docs.awaithumans.dev/awaitverify/errors",
        )


async def create_upload_session(
    *,
    managed_url: str,
    api_key: str | None,
    page_count: int,
    content_type: str,
) -> UploadSession:
    """POST /api/v1/awaitverify/uploads."""
    import base64  # noqa: PLC0415

    body = {"page_count": page_count, "content_type": content_type}
    data = await _post_json(
        url=f"{managed_url.rstrip('/')}/api/v1/awaitverify/uploads",
        body=body,
        api_key=api_key,
    )
    fragments = [
        FragmentSlot(
            page_index=f["page_index"],
            fragment_index=f["fragment_index"],
            key=f["key"],
            upload_url=f["upload_url"],
            upload_headers=f["upload_headers"],
            expires_at_unix=f["expires_at_unix"],
        )
        for f in data["fragments"]
    ]
    return UploadSession(
        upload_session_id=data["upload_session_id"],
        dek=base64.b64decode(data["dek_b64"]),
        fragments=fragments,
        expires_in_seconds=data["expires_in_seconds"],
    )


async def upload_fragment(
    *,
    slot: FragmentSlot,
    ciphertext: bytes,
    http_timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """PUT a single encrypted fragment to its signed URL."""
    async with httpx.AsyncClient(timeout=http_timeout_seconds) as client:
        resp = await client.put(
            slot.upload_url,
            content=ciphertext,
            headers=slot.upload_headers,
        )
    if resp.status_code not in (200, 201):
        raise ManagedBackendError(
            status_code=resp.status_code, body=resp.text, endpoint=slot.upload_url
        )


async def create_task(
    *,
    managed_url: str,
    api_key: str | None,
    upload_session_id: str,
    task_description: str,
    response_schema_json: str,
    priority: str,
    task_metadata: dict[str, str] | None = None,
    initial_response: dict[str, Any] | None = None,
) -> CreatedTask:
    """POST /api/v1/awaitverify/tasks.

    ``initial_response`` carries the customer's prior extraction (Flow A)
    or the SDK-run extraction output (Flow B) as a JSON payload matching
    ``response_schema``. Managed validates it server-side and forwards
    to the OSS reviewer dashboard, which mounts the form with the values
    pre-populated. Omit (or pass None) for pure-human review.
    """
    body: dict[str, Any] = {
        "upload_session_id": upload_session_id,
        "task_description": task_description,
        "response_schema_json": response_schema_json,
        "priority": priority,
    }
    # Only include task_metadata / initial_response when set so the
    # managed backend's schema validation doesn't see an ambiguous
    # explicit null where "omitted" is the correct signal.
    if task_metadata:
        body["task_metadata"] = task_metadata
    if initial_response is not None:
        body["initial_response"] = initial_response
    data = await _post_json(
        url=f"{managed_url.rstrip('/')}/api/v1/awaitverify/tasks",
        body=body,
        api_key=api_key,
    )
    return CreatedTask(
        task_id=data["task_id"],
        upload_session_id=data["upload_session_id"],
        status=data["status"],
    )


async def poll_task(
    *,
    managed_url: str,
    api_key: str | None,
    task_id: str,
    timeout_seconds: int = 25,
) -> PolledTask:
    """GET /api/v1/awaitverify/tasks/{task_id}/poll."""
    url = (
        f"{managed_url.rstrip('/')}/api/v1/awaitverify/tasks/"
        f"{task_id}/poll?timeout={timeout_seconds}"
    )
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=timeout_seconds + 10.0) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        raise ManagedBackendError(status_code=resp.status_code, body=resp.text, endpoint=url)
    data = resp.json()
    return PolledTask(
        task_id=data["task_id"],
        status=data["status"],
        response_json=data.get("response_json"),
    )


async def _post_json(
    *,
    url: str,
    body: dict[str, Any],
    api_key: str | None,
    http_timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=http_timeout_seconds) as client:
        resp = await client.post(url, json=body, headers=headers)
    if resp.status_code not in (200, 201):
        raise ManagedBackendError(status_code=resp.status_code, body=resp.text, endpoint=url)
    return resp.json()  # type: ignore[no-any-return]
