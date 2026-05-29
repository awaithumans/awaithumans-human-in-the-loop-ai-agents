"""Tests for verify_document and the AwaitHumans client class.

The Phase 2 swap replaced the base64-via-await_human transport with
the managed-backend signed-URL flow. These tests stub the four
managed-client calls so the SDK loop can be exercised without a real
backend.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

PIL = pytest.importorskip("PIL", reason="Pillow not installed; skip client tests")
from PIL import Image  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import awaithumans  # noqa: E402
from awaithumans.awaitverify import client as client_mod  # noqa: E402
from awaithumans.awaitverify.client import VerifyTimeoutRangeError  # noqa: E402
from awaithumans.awaitverify.errors import VerifyDocumentArgError  # noqa: E402
from awaithumans.awaitverify.types import Priority  # noqa: E402
from awaithumans.instance import AwaitHumans  # noqa: E402
from awaithumans.providers import (  # noqa: E402
    Anthropic,
    AzureDI,
    DoclingExtraction,
    OpenAI,
    OpenAIExtraction,
    OpenAIStructuring,
    PaddleOCRExtraction,
    Reducto,
    ReductoExtraction,
)
from awaithumans.utils.constants import (  # noqa: E402
    AWAITVERIFY_DEFAULT_TIMEOUT_SECONDS,
    AWAITVERIFY_MIN_TIMEOUT_SECONDS,
)

assert AWAITVERIFY_DEFAULT_TIMEOUT_SECONDS == 48 * 60 * 60  # 48h default
assert AWAITVERIFY_MIN_TIMEOUT_SECONDS == 24 * 60 * 60  # 24h floor


class _StubResponse(BaseModel):
    ok: bool = True


class _Extraction(BaseModel):
    codes: list[str]


def _png_bytes() -> bytes:
    img = Image.new("RGB", (40, 40), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _stub_managed_calls(
    monkeypatch: pytest.MonkeyPatch,
    *,
    completed_response: dict[str, Any] | None = None,
    completed_status: str = "completed",
) -> dict[str, Any]:
    """Replace the four managed-client helpers with deterministic stubs.

    Captures the args each helper was called with so tests can assert
    behavior without a running backend. The completion is delivered on
    the first poll so tests run quickly.
    """
    import base64
    import os

    captured: dict[str, Any] = {
        "uploads_request": None,
        "uploaded_fragments": [],
        "task_request": None,
        "polls": 0,
    }

    # Stub create_upload_session: return a fake 32-byte DEK + N slots.
    async def fake_create_upload_session(
        *, managed_url: str, api_key: str | None, page_count: int, content_type: str
    ) -> Any:
        from awaithumans.awaitverify._managed_client import (
            FragmentSlot,
            UploadSession,
        )

        captured["uploads_request"] = {
            "managed_url": managed_url,
            "api_key": api_key,
            "page_count": page_count,
            "content_type": content_type,
        }
        fragments = []
        for p in range(page_count):
            for f in range(5):
                fragments.append(
                    FragmentSlot(
                        page_index=p,
                        fragment_index=f,
                        key=f"anon/sess/{p:03d}-{f}.png",
                        upload_url=f"http://stub/{p}-{f}",
                        upload_headers={"Content-Type": content_type},
                        expires_at_unix=9_999_999_999,
                    )
                )
        return UploadSession(
            upload_session_id="upload-sess-id",
            dek=os.urandom(32),
            fragments=fragments,
            expires_in_seconds=3600,
        )

    async def fake_upload_fragment(*, slot: Any, ciphertext: bytes) -> None:
        captured["uploaded_fragments"].append(
            (slot.page_index, slot.fragment_index, len(ciphertext))
        )

    async def fake_create_task(
        *,
        managed_url: str,
        api_key: str | None,
        upload_session_id: str,
        task_description: str,
        response_schema_json: str,
        priority: str,
    ) -> Any:
        from awaithumans.awaitverify._managed_client import CreatedTask

        captured["task_request"] = {
            "managed_url": managed_url,
            "api_key": api_key,
            "upload_session_id": upload_session_id,
            "task_description": task_description,
            "response_schema_json": response_schema_json,
            "priority": priority,
        }
        return CreatedTask(
            task_id="task-id-fake",
            upload_session_id=upload_session_id,
            status="awaiting_review",
        )

    async def fake_poll_task(
        *,
        managed_url: str,
        api_key: str | None,
        task_id: str,
        timeout_seconds: int,
    ) -> Any:
        from awaithumans.awaitverify._managed_client import PolledTask

        captured["polls"] += 1
        response_json = (
            json.dumps(completed_response)
            if completed_response is not None and completed_status == "completed"
            else None
        )
        return PolledTask(task_id=task_id, status=completed_status, response_json=response_json)

    monkeypatch.setattr(client_mod, "_managed_create_upload_session", fake_create_upload_session)
    monkeypatch.setattr(client_mod, "_managed_upload_fragment", fake_upload_fragment)
    monkeypatch.setattr(client_mod, "_managed_create_task", fake_create_task)
    monkeypatch.setattr(client_mod, "_managed_poll_task", fake_poll_task)
    # Used in encrypt_fragment under the hood — leave alone, it's real.
    _ = base64
    return captured


class TestAwaitHumansClient:
    def test_constructs_with_api_key(self) -> None:
        client = AwaitHumans(api_key="ah_sk_test")
        assert client.api_key == "ah_sk_test"
        assert client.openai is None

    def test_managed_url_default(self) -> None:
        client = AwaitHumans(api_key="ah_sk_test")
        assert client.managed_url == (
            "https://awaithumans-managed.icyflower-6900d175.westus3."
            "azurecontainerapps.io"
        )

    def test_managed_url_override(self) -> None:
        client = AwaitHumans(api_key="ah_sk_test", managed_url="http://localhost:8000")
        assert client.managed_url == "http://localhost:8000"

    def test_typed_provider_kwarg(self) -> None:
        client = AwaitHumans(api_key="ah_sk_test", openai=OpenAI(api_key="sk-test"))
        assert client.openai is not None
        assert client.openai.api_key == "sk-test"

    def test_multiple_providers_configured(self) -> None:
        client = AwaitHumans(
            api_key="ah_sk_test",
            openai=OpenAI(api_key="sk-openai"),
            anthropic=Anthropic(api_key="sk-ant"),
            reducto=Reducto(api_key="red-key"),
            azure_di=AzureDI(api_key="az-key", endpoint="https://az.cognitiveservices.azure.com"),
        )
        assert client.openai.api_key == "sk-openai"
        assert client.anthropic.api_key == "sk-ant"
        assert client.reducto.api_key == "red-key"
        assert client.azure_di.endpoint.startswith("https://az.")


class TestOCRProvidersRequireStructuring:
    def test_docling_requires_structuring(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            DoclingExtraction()  # type: ignore[call-arg]

    def test_paddle_ocr_requires_structuring(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            PaddleOCRExtraction()  # type: ignore[call-arg]

    def test_docling_with_structuring_constructs(self) -> None:
        DoclingExtraction(
            structuring=OpenAIStructuring(
                model="gpt-5",
                prompt="Structure into schema.",
            ),
        )


class TestLLMProvidersSingleCall:
    def test_reducto_no_structuring_required(self) -> None:
        cfg = ReductoExtraction(prompt="Extract codes.")
        assert cfg.prompt == "Extract codes."


class TestAliasesArePublic:
    def test_module_level_verify_aliases(self) -> None:
        assert awaithumans.awaitVerify is awaithumans.verify_document

    def test_module_level_await_human_aliases(self) -> None:
        assert awaithumans.awaitHuman is awaithumans.await_human


class TestArgValidation:
    @pytest.mark.asyncio
    async def test_rejects_zero_document_sources(self) -> None:
        with pytest.raises(VerifyDocumentArgError):
            await client_mod.verify_document(
                task_description="x",
                response_schema=_StubResponse,
            )

    @pytest.mark.asyncio
    async def test_rejects_two_document_sources(self, tmp_path: Path) -> None:
        png_path = tmp_path / "doc.png"
        png_path.write_bytes(_png_bytes())
        with pytest.raises(VerifyDocumentArgError):
            await client_mod.verify_document(
                task_description="x",
                response_schema=_StubResponse,
                document_path=png_path,
                document_bytes=_png_bytes(),
            )


class TestNewSDKFlow:
    @pytest.mark.asyncio
    async def test_calls_managed_backend_with_expected_args(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = _stub_managed_calls(
            monkeypatch,
            completed_response={"ok": True},
            completed_status="completed",
        )
        png_path = tmp_path / "doc.png"
        png_path.write_bytes(_png_bytes())

        client = AwaitHumans(api_key="ah_sk_test", managed_url="http://localhost:8000")
        result = await client.verify_document(
            task_description="check the codes",
            response_schema=_StubResponse,
            document_path=png_path,
            prior_extraction=_Extraction(codes=["T12C3"]),
            priority="high",
        )

        assert isinstance(result, _StubResponse)
        assert result.ok is True

        # /uploads got the page count and content type
        assert captured["uploads_request"]["page_count"] == 1
        assert captured["uploads_request"]["content_type"] == "image/png"
        assert captured["uploads_request"]["api_key"] == "ah_sk_test"
        assert captured["uploads_request"]["managed_url"] == "http://localhost:8000"

        # 1 page × 5 fragments uploaded
        assert len(captured["uploaded_fragments"]) == 5
        # Each ciphertext is longer than the plaintext (AES-GCM nonce + tag)
        for _, _, length in captured["uploaded_fragments"]:
            assert length > 28  # at least nonce(12) + tag(16)

        # /tasks called with the upload session id from the prior step
        assert captured["task_request"]["upload_session_id"] == "upload-sess-id"
        assert captured["task_request"]["task_description"] == "check the codes"
        assert captured["task_request"]["priority"] == "high"

        # The response_schema flowed as JSON
        schema = json.loads(captured["task_request"]["response_schema_json"])
        assert "properties" in schema

    @pytest.mark.asyncio
    async def test_timed_out_status_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from awaithumans.awaitverify.client import VerifyTaskNonTerminalError

        _stub_managed_calls(monkeypatch, completed_status="timed_out")
        png_path = tmp_path / "doc.png"
        png_path.write_bytes(_png_bytes())

        client = AwaitHumans(api_key="ah_sk_test")
        with pytest.raises(VerifyTaskNonTerminalError):
            await client.verify_document(
                task_description="x",
                response_schema=_StubResponse,
                document_path=png_path,
            )

    @pytest.mark.asyncio
    async def test_priority_string_propagates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = _stub_managed_calls(monkeypatch, completed_response={"ok": True})
        png_path = tmp_path / "doc.png"
        png_path.write_bytes(_png_bytes())

        client = AwaitHumans(api_key="ah_sk_test")
        await client.verify_document(
            task_description="x",
            response_schema=_StubResponse,
            document_path=png_path,
            priority=Priority.HIGH,
        )
        assert captured["task_request"]["priority"] == "high"


class TestTimeoutHandling:
    @pytest.mark.asyncio
    async def test_below_floor_raises(self, tmp_path: Path) -> None:
        png_path = tmp_path / "doc.png"
        png_path.write_bytes(_png_bytes())

        client = AwaitHumans(api_key="ah_sk_test")
        with pytest.raises(VerifyTimeoutRangeError):
            await client.verify_document(
                task_description="x",
                response_schema=_StubResponse,
                document_path=png_path,
                timeout_seconds=AWAITVERIFY_MIN_TIMEOUT_SECONDS - 1,
            )


class TestRejectsBothFlowAandFlowB:
    @pytest.mark.asyncio
    async def test_rejects_combo(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _stub_managed_calls(monkeypatch, completed_response={"ok": True})
        png_path = tmp_path / "doc.png"
        png_path.write_bytes(_png_bytes())
        client = AwaitHumans(api_key="ah_sk_test", openai=OpenAI(api_key="sk-test"))

        with pytest.raises(VerifyDocumentArgError):
            await client.verify_document(
                task_description="x",
                response_schema=_StubResponse,
                document_path=png_path,
                prior_extraction=_Extraction(codes=["T12C3"]),
                extraction=OpenAIExtraction(model="gpt-4o", prompt="extract"),
            )
