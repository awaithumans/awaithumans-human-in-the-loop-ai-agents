"""DashboardStaticFiles — Next.js static-export clean-URL routing.

Pins the regression: `/setup` → `setup.html`, not 404. Covers the
whole dashboard surface, plus "real" 404s for missing assets.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette

from awaithumans.server.core.dashboard_static import DashboardStaticFiles


@pytest.fixture
def dashboard_dir(tmp_path: Path) -> Path:
    """Fake Next static-export output:

        dashboard_dist/
          index.html
          setup.html            ← the real page
          setup/                ← Next metadata sibling dir
            __next.setup.txt
          settings.html
          settings/
            __next.settings.txt
          task.html
          _next/static/chunks/foo.js

    Crucially mirrors the bug surfaced during E2E: Next emits BOTH a
    `<route>.html` file AND a `<route>/` directory (with metadata
    blobs, no `index.html`). Stock `StaticFiles(html=True)` resolves
    the directory first, tries `<route>/index.html`, 404s — even
    though `<route>.html` is right there.
    """
    dist = tmp_path / "dashboard_dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>root</body></html>")

    # Page + metadata-sibling-dir pairs (the real Next layout).
    for name, marker in (
        ("setup", "setup page"),
        ("settings", "settings page"),
        ("task", "task page"),
    ):
        (dist / f"{name}.html").write_text(f"<html><body>{marker}</body></html>")
        meta = dist / name
        meta.mkdir()
        (meta / f"__next.{name}.txt").write_text("(metadata)")

    chunks = dist / "_next" / "static" / "chunks"
    chunks.mkdir(parents=True)
    (chunks / "foo.js").write_text("console.log('ok')")
    return dist


@pytest_asyncio.fixture
async def client(dashboard_dir: Path) -> AsyncGenerator[AsyncClient, None]:
    app = Starlette()
    app.mount(
        "/",
        DashboardStaticFiles(directory=str(dashboard_dir), html=True),
        name="dashboard",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_root_serves_index(client: AsyncClient) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    assert "root" in r.text


@pytest.mark.asyncio
async def test_clean_url_falls_back_to_html_file(client: AsyncClient) -> None:
    """The bug we're fixing: `/setup` → `setup.html`, not 404."""
    r = await client.get("/setup")
    assert r.status_code == 200
    assert "setup page" in r.text


@pytest.mark.asyncio
async def test_every_dashboard_route_resolves(client: AsyncClient) -> None:
    for path, marker in (
        ("/setup", "setup page"),
        ("/settings", "settings page"),
        ("/task", "task page"),
    ):
        r = await client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        assert marker in r.text


@pytest.mark.asyncio
async def test_clean_url_with_query_string(client: AsyncClient) -> None:
    """Query params aren't part of the path — `/setup?token=abc` still
    resolves to `setup.html`."""
    r = await client.get("/setup?token=abc123")
    assert r.status_code == 200
    assert "setup page" in r.text


@pytest.mark.asyncio
async def test_exact_html_path_still_works(client: AsyncClient) -> None:
    r = await client.get("/setup.html")
    assert r.status_code == 200
    assert "setup page" in r.text


@pytest.mark.asyncio
async def test_asset_404_stays_404(client: AsyncClient) -> None:
    """The fallback only kicks in for extensionless paths — a missing
    JS file must not get an `.html` rewrite."""
    r = await client.get("/_next/static/chunks/not-a-real-file.js")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_existing_asset_still_served(client: AsyncClient) -> None:
    r = await client.get("/_next/static/chunks/foo.js")
    assert r.status_code == 200
    assert "console.log" in r.text


@pytest.mark.asyncio
async def test_unknown_clean_path_stays_404(client: AsyncClient) -> None:
    """A path with no matching file AND no `.html` sibling still 404s."""
    r = await client.get("/does-not-exist")
    assert r.status_code == 404
