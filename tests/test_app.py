"""Tests for visual-review app — FastAPI endpoint tests."""

import base64
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure the app module is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app, _cache, _resolve_repo, _base36_decode, _base36_encode, _mime_for_path, _image_cache_control, _EXT_MIME, IMAGE_EXTENSIONS, _gh_paginate, _GitHubError, _GH_PAGE_SIZE


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the in-memory cache between tests."""
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def anyio_backend():
    return "asyncio"


# -- Health endpoint -----------------------------------------------------------

class TestHealth:
    @pytest.mark.asyncio
    async def test_health_check(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# -- Root endpoint -------------------------------------------------------------

class TestRoot:
    @pytest.mark.asyncio
    async def test_root_returns_usage(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["app"] == "Visual Review"
        assert "/{owner}/{repo}/pr/{number}" in data["usage"]


# -- Visual review page -------------------------------------------------------

class TestVisualReviewPage:
    @pytest.mark.asyncio
    async def test_visual_review_returns_html(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/owner/repo/pr/123")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")


# -- PR images endpoint -------------------------------------------------------

class TestPrImages:
    @pytest.mark.asyncio
    async def test_pr_images_no_token(self):
        with patch("app.GITHUB_TOKEN", ""):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/123/images")
        data = resp.json()
        assert "error" in data
        assert data["images"] == []

    @pytest.mark.asyncio
    async def test_pr_images_success(self):
        mock_pr_resp = MagicMock()
        mock_pr_resp.status_code = 200
        mock_pr_resp.json.return_value = {
            "base": {"sha": "aaa", "label": "main", "repo": {"id": 12345}},
            "head": {"sha": "bbb", "label": "feature"},
            "title": "Test PR",
            "html_url": "http://gh/pr/1",
        }

        mock_files_resp = MagicMock()
        mock_files_resp.status_code = 200
        mock_files_resp.json.return_value = [
            {"filename": "tests/screenshots/baseline/test.png", "status": "modified"},
            {"filename": "src/main.py", "status": "modified"},
            {"filename": "tests/screenshots/baseline/new.PNG", "status": "added"},
            {"filename": "photos/hero.jpg", "status": "modified"},
            {"filename": "photos/banner.jpeg", "status": "added"},
            {"filename": "photos/thumb.JPG", "status": "modified"},
            {"filename": "tests/screenshots/baseline/page.bmp", "status": "added"},
            {"filename": "docs/readme.txt", "status": "modified"},
        ]

        async def mock_get(url, **kwargs):
            # /files before /pulls/: the files endpoint is nested under it.
            if url.endswith("/files"):
                return mock_files_resp
            if "/pulls/" in url:
                return mock_pr_resp
            return MagicMock(status_code=404)

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get = mock_get
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/images")

        data = resp.json()
        assert data["base_ref"] == "aaa"
        assert data["head_ref"] == "bbb"
        assert data["repo_id"] == 12345
        # Only image files (.png, .jpg, .jpeg, .bmp) should be included (6 out of 8)
        assert len(data["images"]) == 6
        assert data["images"][0]["path"] == "tests/screenshots/baseline/test.png"
        assert data["images"][1]["path"] == "tests/screenshots/baseline/new.PNG"
        assert data["images"][2]["path"] == "photos/hero.jpg"
        assert data["images"][3]["path"] == "photos/banner.jpeg"
        assert data["images"][4]["path"] == "photos/thumb.JPG"
        assert data["images"][5]["path"] == "tests/screenshots/baseline/page.bmp"

    @pytest.mark.asyncio
    async def test_pr_images_renamed_file(self):
        """Renamed image files include previous_filename in the response."""
        mock_pr_resp = MagicMock()
        mock_pr_resp.status_code = 200
        mock_pr_resp.json.return_value = {
            "base": {"sha": "aaa", "label": "main", "repo": {"id": 12345}},
            "head": {"sha": "bbb", "label": "feature"},
            "title": "Test PR",
            "html_url": "http://gh/pr/1",
        }

        mock_files_resp = MagicMock()
        mock_files_resp.status_code = 200
        mock_files_resp.json.return_value = [
            {
                "filename": "screenshots/new_name.png",
                "status": "renamed",
                "previous_filename": "screenshots/old_name.png",
            },
            {
                "filename": "photos/moved.jpg",
                "status": "renamed",
                "previous_filename": "old_photos/moved.jpg",
            },
        ]

        async def mock_get(url, **kwargs):
            # /files before /pulls/: the files endpoint is nested under it.
            if url.endswith("/files"):
                return mock_files_resp
            if "/pulls/" in url:
                return mock_pr_resp
            return MagicMock(status_code=404)

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get = mock_get
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/images")

        data = resp.json()
        assert len(data["images"]) == 2
        assert data["images"][0]["status"] == "renamed"
        assert data["images"][0]["previous_filename"] == "screenshots/old_name.png"
        assert data["images"][1]["previous_filename"] == "old_photos/moved.jpg"

    @pytest.mark.asyncio
    async def test_pr_images_modified_no_previous_filename(self):
        """Non-renamed files should not include previous_filename."""
        mock_pr_resp = MagicMock()
        mock_pr_resp.status_code = 200
        mock_pr_resp.json.return_value = {
            "base": {"sha": "aaa", "label": "main", "repo": {"id": 12345}},
            "head": {"sha": "bbb", "label": "feature"},
            "title": "Test PR",
            "html_url": "http://gh/pr/1",
        }

        mock_files_resp = MagicMock()
        mock_files_resp.status_code = 200
        mock_files_resp.json.return_value = [
            {"filename": "test.png", "status": "modified"},
        ]

        async def mock_get(url, **kwargs):
            # /files before /pulls/: the files endpoint is nested under it.
            if url.endswith("/files"):
                return mock_files_resp
            if "/pulls/" in url:
                return mock_pr_resp
            return MagicMock(status_code=404)

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get = mock_get
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/images")

        data = resp.json()
        assert len(data["images"]) == 1
        assert "previous_filename" not in data["images"][0]

    @pytest.mark.asyncio
    async def test_pr_images_pr_not_found(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/999/images")

        data = resp.json()
        assert "error" in data
        assert data["images"] == []


# -- Paginated file listing (#12) ---------------------------------------------

def _paged_client(pages, pr_data=None, status_by_page=None):
    """Mock an httpx.AsyncClient serving ``pages`` from pulls/{n}/files.

    ``pages`` is a list of per-page payloads, 1-indexed by request. A request
    for a page past the end gets ``[]``, which is what GitHub answers. Every
    requested page number is recorded on ``instance.requested_pages`` so a test
    can assert which requests the walk actually made — a walk that stops one
    page early and one that never stops both return plausible lists.

    ``status_by_page`` maps a 1-indexed page number to an HTTP status, for
    driving a failure partway through the walk.
    """
    pr_data = pr_data or {
        "base": {"sha": "aaa", "label": "main", "repo": {"id": 12345}},
        "head": {"sha": "bbb", "label": "feature"},
        "title": "Big PR",
        "html_url": "http://gh/pr/352",
    }
    mock_pr_resp = MagicMock()
    mock_pr_resp.status_code = 200
    mock_pr_resp.json.return_value = pr_data

    requested_pages = []

    async def mock_get(url, **kwargs):
        if url.endswith("/files"):
            params = kwargs.get("params", {})
            page = params.get("page")
            requested_pages.append(page)
            status = (status_by_page or {}).get(page, 200)
            resp = MagicMock()
            resp.status_code = status
            if status == 200:
                resp.json.return_value = pages[page - 1] if 1 <= page <= len(pages) else []
            return resp
        if "/pulls/" in url:
            return mock_pr_resp
        return MagicMock(status_code=404)

    instance = AsyncMock()
    instance.get = mock_get
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    instance.requested_pages = requested_pages
    return instance


def _png_pages(total, per_page=_GH_PAGE_SIZE):
    """Split ``total`` synthetic .png file entries into full pages."""
    files = [
        {"filename": f"baselines/shot_{i:04d}.png", "status": "added"}
        for i in range(total)
    ]
    return [files[i:i + per_page] for i in range(0, len(files), per_page)] or [[]]


class TestPrImagesPagination:
    """#12: a 300+ file PR lost every file past the first page, silently."""

    @pytest.mark.asyncio
    async def test_all_files_returned_past_the_compare_cap(self):
        """489 image files — PR 352's real shape — all come back.

        The compare endpoint this replaced answered that PR with exactly 300
        files and no next page, so 189 went missing with nothing said.
        """
        instance = _paged_client(_png_pages(489))

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/352/images")

        data = resp.json()
        assert len(data["images"]) == 489
        assert data["images"][0]["path"] == "baselines/shot_0000.png"
        assert data["images"][-1]["path"] == "baselines/shot_0488.png"
        assert data["truncated"] is False
        # Five pages: four full, then an 89-entry page that ends the walk.
        assert instance.requested_pages == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_hits_the_files_endpoint_not_compare(self):
        """The compare endpoint cannot serve this, so nothing may call it."""
        seen = []

        mock_pr_resp = MagicMock()
        mock_pr_resp.status_code = 200
        mock_pr_resp.json.return_value = {
            "base": {"sha": "aaa", "label": "main", "repo": {"id": 12345}},
            "head": {"sha": "bbb", "label": "feature"},
            "title": "Test PR",
            "html_url": "http://gh/pr/1",
        }

        async def mock_get(url, **kwargs):
            seen.append(url)
            if url.endswith("/files"):
                r = MagicMock(status_code=200)
                r.json.return_value = [{"filename": "a.png", "status": "added"}]
                return r
            if "/pulls/" in url:
                return mock_pr_resp
            return MagicMock(status_code=404)

        instance = AsyncMock()
        instance.get = mock_get
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/images")

        assert resp.json()["images"] == [
            {"path": "a.png", "status": "added", "additions": 0, "deletions": 0}
        ]
        assert not any("/compare/" in url for url in seen)
        assert any(url.endswith("/pulls/1/files") for url in seen)

    @pytest.mark.asyncio
    async def test_exact_page_multiple_needs_the_empty_page(self):
        """A full last page is indistinguishable from a middle one.

        200 files is two full pages; only the empty third page ends the walk,
        so the response must not stop at 100.
        """
        instance = _paged_client(_png_pages(200))

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/images")

        data = resp.json()
        assert len(data["images"]) == 200
        assert instance.requested_pages == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_files_request_failure_is_reported(self):
        """A page that fails partway through is an error, not a short list."""
        instance = _paged_client(_png_pages(250), status_by_page={2: 500})

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/images")

        data = resp.json()
        assert data["images"] == []
        assert "500" in data["error"]
        # It stopped at the failure rather than walking on past it.
        assert instance.requested_pages == [1, 2]


class TestGhPaginate:
    """The walk itself, driven directly so every branch is reachable."""

    def _client(self, pages, status_by_page=None):
        requested = []

        async def mock_get(url, **kwargs):
            page = kwargs.get("params", {}).get("page")
            requested.append(page)
            status = (status_by_page or {}).get(page, 200)
            resp = MagicMock()
            resp.status_code = status
            if status == 200:
                resp.json.return_value = pages[page - 1] if 1 <= page <= len(pages) else []
            return resp

        client = AsyncMock()
        client.get = mock_get
        client.requested = requested
        return client

    @pytest.mark.asyncio
    async def test_single_short_page(self):
        client = self._client([[1, 2, 3]])
        items, truncated = await _gh_paginate(client, "http://gh/list", {})
        assert items == [1, 2, 3]
        assert truncated is False
        assert client.requested == [1]

    @pytest.mark.asyncio
    async def test_requests_per_page_100(self):
        """Asking for more than 100 is clamped by GitHub, so ask for 100."""
        captured = {}

        async def mock_get(url, **kwargs):
            captured.update(kwargs.get("params", {}))
            resp = MagicMock(status_code=200)
            resp.json.return_value = []
            return resp

        client = AsyncMock()
        client.get = mock_get
        await _gh_paginate(client, "http://gh/list", {})
        assert captured["per_page"] == 100

    @pytest.mark.asyncio
    async def test_caller_params_survive_paging(self):
        captured = []

        async def mock_get(url, **kwargs):
            captured.append(dict(kwargs.get("params", {})))
            resp = MagicMock(status_code=200)
            resp.json.return_value = []
            return resp

        client = AsyncMock()
        client.get = mock_get
        await _gh_paginate(client, "http://gh/list", {}, params={"state": "all"})
        assert captured[0]["state"] == "all"
        assert captured[0]["page"] == 1

    @pytest.mark.asyncio
    async def test_truncates_at_max_pages(self):
        """max_pages is a parameter so this block is reachable at all.

        Three full pages with max_pages=2 stops with more still to read.
        """
        client = self._client([list(range(_GH_PAGE_SIZE))] * 3)
        items, truncated = await _gh_paginate(client, "http://gh/list", {}, max_pages=2)
        assert len(items) == 2 * _GH_PAGE_SIZE
        assert truncated is True
        assert client.requested == [1, 2]

    @pytest.mark.asyncio
    async def test_last_page_exactly_full_is_not_truncated(self):
        """Stopping on the cap is only truncation if more remained."""
        client = self._client([list(range(_GH_PAGE_SIZE)), []])
        items, truncated = await _gh_paginate(client, "http://gh/list", {}, max_pages=2)
        assert len(items) == _GH_PAGE_SIZE
        assert truncated is False
        assert client.requested == [1, 2]

    @pytest.mark.asyncio
    async def test_non_200_raises(self):
        client = self._client([[1]], status_by_page={1: 404})
        with pytest.raises(_GitHubError) as exc:
            await _gh_paginate(client, "http://gh/list", {})
        assert exc.value.status_code == 404
        assert "404" in str(exc.value)

    @pytest.mark.asyncio
    async def test_non_list_body_raises(self):
        """A dict body would otherwise extend the result with its keys."""
        client = self._client([{"message": "Not Found"}])
        with pytest.raises(_GitHubError) as exc:
            await _gh_paginate(client, "http://gh/list", {})
        assert exc.value.status_code is None
        assert "dict" in str(exc.value)


# -- PR image proxy endpoint --------------------------------------------------

class TestPrImage:
    @pytest.mark.asyncio
    async def test_pr_image_no_token(self):
        with patch("app.GITHUB_TOKEN", ""):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/image?path=test.png&ref=abc")
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_pr_image_base64_success(self):
        img_data = b"fake-png-data"
        b64_data = base64.b64encode(img_data).decode()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"encoding": "base64", "content": b64_data}

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/image?path=test.png&ref=abc123")

        assert resp.status_code == 200
        assert resp.content == img_data
        assert "image/png" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_pr_image_blob_fallback_for_large_file(self):
        """When GitHub contents API omits base64 content for large files,
        the endpoint should fall back to the Git Blob API."""
        img_data = b"large-fake-png-data"
        b64_data = base64.b64encode(img_data).decode()

        mock_contents_resp = MagicMock()
        mock_contents_resp.status_code = 200
        mock_contents_resp.json.return_value = {
            "sha": "deadbeef123",
            "size": 2_000_000,
            "download_url": "https://raw.githubusercontent.com/owner/repo/abc/test.png",
        }

        mock_blob_resp = MagicMock()
        mock_blob_resp.status_code = 200
        mock_blob_resp.json.return_value = {
            "encoding": "base64",
            "content": b64_data,
        }

        async def mock_get(url, **kwargs):
            if "/git/blobs/" in url:
                return mock_blob_resp
            return mock_contents_resp

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get = mock_get
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/image?path=large.png&ref=abc123")

        assert resp.status_code == 200
        assert resp.content == img_data
        assert "image/png" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_pr_image_jpeg_content_type(self):
        """JPEG files should be served with image/jpeg content type."""
        img_data = b"fake-jpeg-data"
        b64_data = base64.b64encode(img_data).decode()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"encoding": "base64", "content": b64_data}

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/image?path=photo.jpg&ref=abc123")

        assert resp.status_code == 200
        assert resp.content == img_data
        assert "image/jpeg" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_pr_image_jpeg_extension_content_type(self):
        """Files with .jpeg extension should also be served with image/jpeg."""
        img_data = b"fake-jpeg-data"
        b64_data = base64.b64encode(img_data).decode()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"encoding": "base64", "content": b64_data}

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/image?path=banner.jpeg&ref=abc123")

        assert resp.status_code == 200
        assert resp.content == img_data
        assert "image/jpeg" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_pr_image_bmp_content_type(self):
        """BMP files should be served with image/bmp content type."""
        img_data = b"BM\x00\x00fake-bmp-data"
        b64_data = base64.b64encode(img_data).decode()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"encoding": "base64", "content": b64_data}

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/image?path=baseline/test.bmp&ref=abc123")

        assert resp.status_code == 200
        assert resp.content == img_data
        assert "image/bmp" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_pr_image_github_404(self):
        """When the file doesn't exist at the given ref, return 404."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.return_value = {"message": "Not Found"}

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/image?path=missing.png&ref=abc123")

        assert resp.status_code == 404


# -- PR comments endpoints ----------------------------------------------------

class TestPrComments:
    @pytest.mark.asyncio
    async def test_get_comments_no_token(self):
        with patch("app.GITHUB_TOKEN", ""):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/comments?path=test.png")
        data = resp.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_get_comments_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "id": 1,
                "body": "Looks good",
                "user": {"login": "reviewer"},
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "path": "test.png",
                "html_url": "http://gh/comment/1",
            },
            {
                "id": 2,
                "body": "Different file",
                "user": {"login": "other"},
                "created_at": "2026-01-01T00:00:00Z",
                "path": "other.png",
                "html_url": "http://gh/comment/2",
            },
        ]

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/comments?path=test.png")

        data = resp.json()
        assert len(data["comments"]) == 1
        assert data["comments"][0]["body"] == "Looks good"
        assert data["comments"][0]["user"] == "reviewer"


class TestPrCommentCounts:
    @pytest.mark.asyncio
    async def test_comment_counts_no_token(self):
        with patch("app.GITHUB_TOKEN", ""):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/comment-counts")
        data = resp.json()
        assert data["counts"] == {}

    @pytest.mark.asyncio
    async def test_comment_counts_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"path": "test.png"},
            {"path": "test.png"},
            {"path": "other.png"},
        ]

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/comment-counts")

        data = resp.json()
        assert data["counts"]["test.png"] == 2
        assert data["counts"]["other.png"] == 1


class TestPostPrComment:
    @pytest.mark.asyncio
    async def test_post_comment_no_token(self):
        with patch("app.GITHUB_TOKEN", ""):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post("/api/owner/repo/pr/1/comments", json={"path": "test.png", "body": "Nice"})
        data = resp.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_post_comment_missing_fields(self):
        with patch("app.GITHUB_TOKEN", "fake-token"):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post("/api/owner/repo/pr/1/comments", json={"path": "", "body": ""})
        data = resp.json()
        assert "error" in data
        assert "required" in data["error"]

    @pytest.mark.asyncio
    async def test_post_comment_success(self):
        mock_pr_resp = MagicMock()
        mock_pr_resp.status_code = 200
        mock_pr_resp.json.return_value = {"head": {"sha": "abc123"}}

        mock_comment_resp = MagicMock()
        mock_comment_resp.status_code = 201
        mock_comment_resp.json.return_value = {
            "id": 42,
            "body": "Looks great",
            "user": {"login": "me"},
            "created_at": "2026-01-01T00:00:00Z",
            "html_url": "http://gh/comment/42",
        }

        async def mock_get(url, **kwargs):
            return mock_pr_resp

        async def mock_post(url, **kwargs):
            return mock_comment_resp

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get = mock_get
            instance.post = mock_post
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post(
                    "/api/owner/repo/pr/1/comments",
                    json={"path": "test.png", "body": "Looks great"},
                )

        data = resp.json()
        assert data["ok"] is True
        assert data["comment"]["id"] == 42
        assert data["comment"]["body"] == "Looks great"

    @pytest.mark.asyncio
    async def test_post_comment_invalid_json(self):
        with patch("app.GITHUB_TOKEN", "fake-token"):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post(
                    "/api/owner/repo/pr/1/comments",
                    content=b"not json",
                    headers={"content-type": "application/json"},
                )
        data = resp.json()
        assert "error" in data


# -- Short URL resolution -----------------------------------------------------

class TestResolveRepo:
    @pytest.mark.asyncio
    async def test_resolve_numeric_id(self):
        """Numeric identifier resolves via /repositories/{id}."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "owner": {"login": "widdowson"},
            "name": "visual-review",
        }

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await _resolve_repo("12345")

        assert result == [("widdowson", "visual-review")]

    @pytest.mark.asyncio
    async def test_resolve_numeric_id_not_found(self):
        """Numeric ID that doesn't exist returns empty list."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await _resolve_repo("99999999")

        assert result == []

    @pytest.mark.asyncio
    async def test_resolve_repo_name_unique(self):
        """Repo name with exactly one match resolves successfully."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {"name": "visual-review", "owner": {"login": "widdowson"}},
            ]
        }

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await _resolve_repo("visual-review")

        assert result == [("widdowson", "visual-review")]

    @pytest.mark.asyncio
    async def test_resolve_repo_name_ambiguous(self):
        """Repo name with multiple exact matches returns all matches."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {"name": "myrepo", "owner": {"login": "alice"}},
                {"name": "myrepo", "owner": {"login": "bob"}},
                {"name": "myrepo-extra", "owner": {"login": "charlie"}},
            ]
        }

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await _resolve_repo("myrepo")

        # Only exact matches, not "myrepo-extra"
        assert result == [("alice", "myrepo"), ("bob", "myrepo")]

    @pytest.mark.asyncio
    async def test_resolve_repo_name_no_match(self):
        """Repo name with no matches returns empty list."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"items": []}

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await _resolve_repo("nonexistent-repo")

        assert result == []

    @pytest.mark.asyncio
    async def test_resolve_no_token(self):
        """Without a GitHub token, resolution returns empty."""
        with patch("app.GITHUB_TOKEN", ""):
            result = await _resolve_repo("visual-review")
        assert result == []

    @pytest.mark.asyncio
    async def test_resolve_caches_result(self):
        """Resolved repos are cached; second call doesn't hit API."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "owner": {"login": "widdowson"},
            "name": "visual-review",
        }

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result1 = await _resolve_repo("12345")
            result2 = await _resolve_repo("12345")

        assert result1 == result2
        # httpx.AsyncClient should only be called once (cached on second call)
        assert MockClient.call_count == 1

    @pytest.mark.asyncio
    async def test_resolve_case_insensitive_name_match(self):
        """Name matching is case-insensitive."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {"name": "Visual-Review", "owner": {"login": "widdowson"}},
            ]
        }

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await _resolve_repo("visual-review")

        assert result == [("widdowson", "Visual-Review")]


class TestShortUrlRedirect:
    @pytest.mark.asyncio
    async def test_redirect_by_repo_name(self):
        """Short URL by repo name redirects to canonical path."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {"name": "visual-review", "owner": {"login": "widdowson"}},
            ]
        }

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test", follow_redirects=False
            ) as ac:
                resp = await ac.get("/visual-review/pr/42")

        assert resp.status_code == 302
        assert resp.headers["location"] == "/widdowson/visual-review/pr/42"

    @pytest.mark.asyncio
    async def test_redirect_by_repo_id(self):
        """Short URL by numeric repo ID redirects to canonical path."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "owner": {"login": "widdowson"},
            "name": "visual-review",
        }

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test", follow_redirects=False
            ) as ac:
                resp = await ac.get("/12345/pr/42")

        assert resp.status_code == 302
        assert resp.headers["location"] == "/widdowson/visual-review/pr/42"

    @pytest.mark.asyncio
    async def test_short_url_not_found(self):
        """Unknown repo name returns 404."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"items": []}

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/nonexistent/pr/1")

        assert resp.status_code == 404
        data = resp.json()
        assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_short_url_ambiguous(self):
        """Ambiguous repo name returns 300 with matches listed."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {"name": "myrepo", "owner": {"login": "alice"}},
                {"name": "myrepo", "owner": {"login": "bob"}},
            ]
        }

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/myrepo/pr/5")

        assert resp.status_code == 300
        data = resp.json()
        assert data["error"] == "Ambiguous repository name"
        assert len(data["matches"]) == 2
        assert data["matches"][0]["url"] == "/alice/myrepo/pr/5"
        assert data["matches"][1]["url"] == "/bob/myrepo/pr/5"

    @pytest.mark.asyncio
    async def test_redirect_by_base36_id(self):
        """Short URL with base36-encoded repo ID redirects to canonical path."""
        mock_search_resp = MagicMock()
        mock_search_resp.status_code = 200
        mock_search_resp.json.return_value = {"items": []}  # No name match

        mock_repo_resp = MagicMock()
        mock_repo_resp.status_code = 200
        mock_repo_resp.json.return_value = {
            "owner": {"login": "widdowson"},
            "name": "apwphotos-appv2",
        }

        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "/search/" in url:
                return mock_search_resp
            if "/repositories/" in url:
                return mock_repo_resp
            return MagicMock(status_code=404)

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get = mock_get
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            # base36 encode of 1125541223 = "im495z"
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test", follow_redirects=False
            ) as ac:
                resp = await ac.get("/im495z/pr/209")

        assert resp.status_code == 302
        assert resp.headers["location"] == "/widdowson/apwphotos-appv2/pr/209"

    @pytest.mark.asyncio
    async def test_canonical_url_still_works(self):
        """The full /{owner}/{repo}/pr/{number} route still works."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/owner/repo/pr/123")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")


class TestMimeForPath:
    def test_png(self):
        assert _mime_for_path("screenshots/test.png") == "image/png"

    def test_png_uppercase(self):
        assert _mime_for_path("screenshots/test.PNG") == "image/png"

    def test_jpg(self):
        assert _mime_for_path("photos/hero.jpg") == "image/jpeg"

    def test_jpeg(self):
        assert _mime_for_path("photos/banner.jpeg") == "image/jpeg"

    def test_jpg_uppercase(self):
        assert _mime_for_path("photos/HERO.JPG") == "image/jpeg"

    def test_bmp(self):
        assert _mime_for_path("screenshots/test.bmp") == "image/bmp"

    def test_bmp_uppercase(self):
        assert _mime_for_path("screenshots/test.BMP") == "image/bmp"

    def test_unknown_defaults_to_png(self):
        assert _mime_for_path("file.tiff") == "image/png"

    def test_no_extension_defaults_to_png(self):
        assert _mime_for_path("noext") == "image/png"


class TestPrChecks:
    @pytest.mark.asyncio
    async def test_checks_no_token(self):
        with patch("app.GITHUB_TOKEN", ""):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/checks")
        data = resp.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_checks_success(self):
        mock_pr_resp = MagicMock()
        mock_pr_resp.status_code = 200
        mock_pr_resp.json.return_value = {"head": {"sha": "abc123def456"}}

        mock_checks_resp = MagicMock()
        mock_checks_resp.status_code = 200
        mock_checks_resp.json.return_value = {
            "check_runs": [
                {
                    "name": "Test",
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "http://gh/runs/1",
                },
                {
                    "name": "Deploy",
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "http://gh/runs/2",
                },
            ]
        }

        async def mock_get(url, **kwargs):
            if "/pulls/" in url:
                return mock_pr_resp
            if "/check-runs" in url:
                return mock_checks_resp
            return MagicMock(status_code=404)

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get = mock_get
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/checks")

        data = resp.json()
        assert data["overall"] == "success"
        assert len(data["runs"]) == 2
        assert data["sha"] == "abc123de"

    @pytest.mark.asyncio
    async def test_checks_failure(self):
        mock_pr_resp = MagicMock()
        mock_pr_resp.status_code = 200
        mock_pr_resp.json.return_value = {"head": {"sha": "abc123def456"}}

        mock_checks_resp = MagicMock()
        mock_checks_resp.status_code = 200
        mock_checks_resp.json.return_value = {
            "check_runs": [
                {"name": "Test", "status": "completed", "conclusion": "failure", "html_url": ""},
                {"name": "Lint", "status": "completed", "conclusion": "success", "html_url": ""},
            ]
        }

        async def mock_get(url, **kwargs):
            if "/pulls/" in url:
                return mock_pr_resp
            return mock_checks_resp

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get = mock_get
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/checks")

        data = resp.json()
        assert data["overall"] == "failure"

    @pytest.mark.asyncio
    async def test_checks_pending(self):
        mock_pr_resp = MagicMock()
        mock_pr_resp.status_code = 200
        mock_pr_resp.json.return_value = {"head": {"sha": "abc123def456"}}

        mock_checks_resp = MagicMock()
        mock_checks_resp.status_code = 200
        mock_checks_resp.json.return_value = {
            "check_runs": [
                {"name": "Test", "status": "in_progress", "conclusion": None, "html_url": ""},
            ]
        }

        async def mock_get(url, **kwargs):
            if "/pulls/" in url:
                return mock_pr_resp
            return mock_checks_resp

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get = mock_get
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/checks")

        data = resp.json()
        assert data["overall"] == "pending"


class TestImageExtensionsJson:
    """Verify that _EXT_MIME is loaded correctly from image_extensions.json."""

    def test_ext_mime_loaded_from_json(self):
        import json
        json_path = Path(__file__).resolve().parent.parent / "image_extensions.json"
        with open(json_path) as f:
            expected = json.load(f)
        assert _EXT_MIME == expected

    def test_all_extensions_start_with_dot(self):
        for ext in IMAGE_EXTENSIONS:
            assert ext.startswith("."), f"Extension must start with dot: {ext}"

    def test_all_mime_types_are_image(self):
        for ext, mime in _EXT_MIME.items():
            assert mime.startswith("image/"), f"{ext} MIME must start with image/, got {mime}"

    def test_known_extensions_present(self):
        assert ".png" in _EXT_MIME
        assert ".jpg" in _EXT_MIME
        assert ".jpeg" in _EXT_MIME
        assert ".bmp" in _EXT_MIME


class TestBase36:
    def test_encode_decode_roundtrip(self):
        assert _base36_decode(_base36_encode(1125541223)) == 1125541223

    def test_encode_known_value(self):
        assert _base36_encode(1125541223) == "im495z"

    def test_decode_known_value(self):
        assert _base36_decode("im495z") == 1125541223

    def test_decode_invalid(self):
        assert _base36_decode("not!valid") is None

    def test_encode_zero(self):
        assert _base36_encode(0) == "0"

    def test_decode_zero(self):
        assert _base36_decode("0") == 0


class TestImageCacheControl:
    """A proxied image is addressed by (path, commit sha), so it can never
    change. Saying so is what lets a revisited file — and one the SPA
    prefetched — cost the proxy nothing."""

    def test_full_sha_is_immutable(self):
        assert _image_cache_control("a" * 40) == "private, max-age=31536000, immutable"

    def test_real_sha_is_immutable(self):
        assert "immutable" in _image_cache_control("fc7c062a72c551c192bac3ad09482f0825812978")

    def test_branch_name_keeps_short_ttl(self):
        # A branch moves, so a long TTL would pin a stale image.
        assert _image_cache_control("main") == "private, max-age=300"

    def test_short_sha_keeps_short_ttl(self):
        assert _image_cache_control("fc7c062") == "private, max-age=300"

    def test_uppercase_sha_keeps_short_ttl(self):
        # GitHub hands us lowercase; anything else did not come from the API.
        assert _image_cache_control("A" * 40) == "private, max-age=300"

    def test_overlong_ref_keeps_short_ttl(self):
        assert _image_cache_control("a" * 41) == "private, max-age=300"

    def test_sha_with_trailing_text_keeps_short_ttl(self):
        assert _image_cache_control("a" * 40 + "/x") == "private, max-age=300"

    def test_sha_with_trailing_newline_keeps_short_ttl(self):
        # re.match(r"...$") would accept this; fullmatch does not.
        assert _image_cache_control("a" * 40 + "\n") == "private, max-age=300"

    def test_empty_ref_keeps_short_ttl(self):
        assert _image_cache_control("") == "private, max-age=300"

    def test_never_public(self):
        # These bytes can come from a private repository; no intermediary
        # should be invited to hold them.
        for ref in ("a" * 40, "main", "", "fc7c062"):
            assert "public" not in _image_cache_control(ref)


class TestPrImageCacheHeader:
    """The header the endpoint actually sends, not just the helper's opinion."""

    @staticmethod
    def _client_with_image(img_data: bytes):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "encoding": "base64",
            "content": base64.b64encode(img_data).decode(),
        }
        instance = AsyncMock()
        instance.get.return_value = mock_resp
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        return instance

    async def _cache_control_for(self, ref: str) -> tuple[int, str | None]:
        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = self._client_with_image(b"fake-png-data")
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get(f"/api/owner/repo/pr/1/image?path=test.png&ref={ref}")
        return resp.status_code, resp.headers.get("cache-control")

    @pytest.mark.asyncio
    async def test_sha_ref_response_is_immutable(self):
        status, cache_control = await self._cache_control_for("a" * 40)
        assert status == 200
        assert cache_control == "private, max-age=31536000, immutable"

    @pytest.mark.asyncio
    async def test_branch_ref_response_keeps_short_ttl(self):
        status, cache_control = await self._cache_control_for("main")
        assert status == 200
        assert cache_control == "private, max-age=300"

    @pytest.mark.asyncio
    async def test_error_response_is_not_cached(self):
        """A 404 must not be pinned for a year — the file may appear later."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get(
                    "/api/owner/repo/pr/1/image?path=missing.png&ref=" + "a" * 40
                )

        assert resp.status_code == 404
        # Not merely "not immutable": the error path attaches no image headers
        # at all, so a file that appears later is not shadowed by a cached 404.
        assert resp.headers.get("cache-control") is None
