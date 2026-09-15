"""Tests for visual-review app — FastAPI endpoint tests."""

import asyncio
import base64
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure the app module is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app, _cache, _resolve_repo, _base36_decode, _base36_encode, _mime_for_path, _image_cache_control, _is_safe_image_path, _EXT_MIME, IMAGE_EXTENSIONS, _is_image_path, _gh_paginate, _GitHubError, _GH_PAGE_SIZE, _GH_MAX_PAGES, _PR_PROBE_CONCURRENCY, _OPEN_PULLS_MAX_PAGES


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
    async def test_truncated_reaches_the_response(self):
        """A capped walk is reported in the payload, not just internally.

        This drives the real ``_GH_MAX_PAGES``: the cap is a default argument
        bound at definition time, so patching the module constant would not
        reach it. 30 full pages is what the endpoint actually stops on.

        Without this, ``result["truncated"] = files_truncated`` could be cut
        to a literal ``False`` with the whole suite green — the only other
        endpoint test reading the key asserts ``False``, which that mutant
        satisfies. Verified: it passed 84/84 before this test existed.
        """
        instance = _paged_client(_png_pages(_GH_PAGE_SIZE * _GH_MAX_PAGES))

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/images")

        data = resp.json()
        assert data["truncated"] is True
        assert len(data["images"]) == _GH_PAGE_SIZE * _GH_MAX_PAGES
        assert instance.requested_pages == list(range(1, _GH_MAX_PAGES + 1))

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
        """A caller's params ride every page, not just the first.

        The fixture has to page for this to mean anything: an earlier
        version answered ``[]`` on page 1, so the walk stopped after one
        request and dropping the caller's params from page 2 onward left
        the suite green.
        """
        captured = []

        async def mock_get(url, **kwargs):
            params = dict(kwargs.get("params", {}))
            captured.append(params)
            resp = MagicMock(status_code=200)
            # Full page 1 forces a second request; short page 2 ends the walk.
            resp.json.return_value = list(range(_GH_PAGE_SIZE)) if params["page"] == 1 else []
            return resp

        client = AsyncMock()
        client.get = mock_get
        await _gh_paginate(client, "http://gh/list", {}, params={"state": "all"})
        assert [p["page"] for p in captured] == [1, 2]
        assert [p["state"] for p in captured] == ["all", "all"]
        assert [p["per_page"] for p in captured] == [_GH_PAGE_SIZE] * 2

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
    async def test_short_page_on_the_last_permitted_page_is_not_truncated(self):
        """A short page ends the walk even when it is the last page allowed.

        The cap is reached here, but the walk stopped because the sequence
        ended, so nothing is flagged. Named for what it drives: an earlier
        version of this test was called ...last_page_exactly_full... while
        its fixture ended on an empty page, so it passed whatever the code
        did at the real boundary. That case is the next test.
        """
        client = self._client([list(range(_GH_PAGE_SIZE)), []])
        items, truncated = await _gh_paginate(client, "http://gh/list", {}, max_pages=2)
        assert len(items) == _GH_PAGE_SIZE
        assert truncated is False
        assert client.requested == [1, 2]

    @pytest.mark.asyncio
    async def test_exactly_max_pages_reports_truncated(self):
        """The boundary: full pages all the way to the cap, nothing beyond.

        Nothing remained, and ``truncated`` is still true. Deliberate — the
        walk never asked for page 4, and settling it would cost a probe that
        GitHub cannot answer meaningfully at its own 3000-file ceiling. The
        flag means "the walk ran out of pages", not "more exists", and it
        errs toward warning. Pinned so the semantics are a decision on the
        record rather than something a later reader has to re-derive.
        """
        client = self._client([list(range(_GH_PAGE_SIZE))] * 3)
        items, truncated = await _gh_paginate(client, "http://gh/list", {}, max_pages=3)
        assert len(items) == 3 * _GH_PAGE_SIZE
        assert truncated is True
        # Page 4 exists in the fixture's eyes only as the empty page the walk
        # never requested.
        assert client.requested == [1, 2, 3]

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

    @pytest.mark.asyncio
    async def test_pr_image_traversal_is_rejected_before_any_request(self):
        """A `..` traversal path is rejected with 400, and no upstream GitHub
        request is made — httpx collapses `../` segments before sending, which
        would otherwise retarget the call at another repository and return its
        bytes under the deployment token (issue #37)."""
        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get(
                    "/api/owner/repo/pr/1/image"
                    "?path=../../../victim/priv/contents/s.png&ref=deadbeef"
                )

        assert resp.status_code == 400
        # The endpoint must never have reached out to GitHub with the crafted path.
        instance.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_pr_image_percent_encoded_traversal_is_rejected(self):
        """`..%2F` decodes to `../` during query parsing, so the decoded value
        this endpoint sees is a literal `..` segment and is rejected."""
        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get(
                    "/api/owner/repo/pr/1/image"
                    "?path=..%2F..%2F..%2Fvictim/priv/contents/s.png&ref=deadbeef"
                )

        assert resp.status_code == 400
        instance.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_pr_image_ordinary_nested_path_still_works(self):
        """A legitimate deep path like the ones the images list returns is
        served normally."""
        img_data = b"nested-png-data"
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
                resp = await ac.get(
                    "/api/owner/repo/pr/1/image"
                    "?path=django/apps/home/tests/visual/x.png&ref=abc123"
                )

        assert resp.status_code == 200
        assert resp.content == img_data


# -- Image path validation ----------------------------------------------------

class TestSafeImagePath:
    def test_ordinary_nested_path(self):
        assert _is_safe_image_path("django/apps/home/tests/visual/x.png")

    def test_bare_filename(self):
        assert _is_safe_image_path("x.png")

    def test_all_image_extensions_accepted(self):
        for ext in IMAGE_EXTENSIONS:
            assert _is_safe_image_path(f"a/b{ext}"), ext

    def test_parent_traversal_rejected(self):
        assert not _is_safe_image_path("../../../victim/priv/contents/s.png")

    def test_mid_path_traversal_rejected(self):
        assert not _is_safe_image_path("a/b/../../../victim/s.png")

    def test_leading_slash_rejected(self):
        assert not _is_safe_image_path("/etc/passwd.png")

    def test_absolute_url_like_rejected(self):
        # A protocol-relative-looking value collapses to an empty segment.
        assert not _is_safe_image_path("//evil.example/x.png")

    def test_double_slash_rejected(self):
        assert not _is_safe_image_path("a//b.png")

    def test_single_dot_segment_rejected(self):
        assert not _is_safe_image_path("./x.png")

    def test_backslash_rejected(self):
        assert not _is_safe_image_path("a\\..\\b.png")

    def test_empty_rejected(self):
        assert not _is_safe_image_path("")

    def test_non_image_extension_rejected(self):
        assert not _is_safe_image_path("secrets/config.yml")

    def test_no_extension_rejected(self):
        assert not _is_safe_image_path("django/apps/home/README")


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


# -- Repo page (#39) ----------------------------------------------------------

def _pull(number, *, title=None, head_sha=None, draft=False, labels=(), author="someone"):
    """One entry as "List pull requests" serves it."""
    return {
        "number": number,
        "title": title or f"PR {number}",
        "user": {"login": author},
        "draft": draft,
        "html_url": f"https://github.com/owner/repo/pull/{number}",
        "updated_at": "2026-09-15T12:00:00Z",
        "head": {"ref": f"feature-{number}", "sha": head_sha or f"sha{number}"},
        "base": {"ref": "main", "sha": "basesha"},
        "labels": [{"name": name} for name in labels],
    }


class _RepoClient:
    """A mock httpx.AsyncClient serving a repo's pulls and per-PR file lists.

    ``pull_pages`` is the paged "List pull requests" response, 1-indexed;
    ``files_by_number`` maps a PR number to its complete file list, which this
    serves in pages of ``_GH_PAGE_SIZE``. ``list_status`` and
    ``files_status`` drive failures.

    Every request is recorded on ``.calls`` as ``(url, page)``, because the
    cost of this page is the whole design constraint: a test that only reads
    the payload cannot tell one request per PR from four, and a cache that
    never hits looks exactly like one that does.
    """

    def __init__(self, pull_pages, files_by_number=None, list_status=None, files_status=None):
        self.pull_pages = pull_pages
        self.files_by_number = files_by_number or {}
        self.list_status = list_status or {}
        self.files_status = files_status or {}
        self.calls = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def get(self, url, **kwargs):
        page = kwargs.get("params", {}).get("page")
        self.calls.append((url, page))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            # Yield to the loop so overlapping requests really do overlap;
            # without this every call runs to completion before the next
            # starts and the concurrency cap could not be observed.
            await asyncio.sleep(0)
            if url.endswith("/files"):
                number = int(url.split("/pulls/")[1].split("/")[0])
                status = self.files_status.get(number, 200)
                resp = MagicMock(status_code=status)
                if status == 200:
                    files = self.files_by_number.get(number, [])
                    start = (page - 1) * _GH_PAGE_SIZE
                    resp.json.return_value = files[start:start + _GH_PAGE_SIZE]
                return resp
            if url.endswith("/pulls"):
                status = self.list_status.get(page, 200)
                resp = MagicMock(status_code=status)
                if status == 200:
                    resp.json.return_value = (
                        self.pull_pages[page - 1] if 1 <= page <= len(self.pull_pages) else []
                    )
                return resp
            return MagicMock(status_code=404)
        finally:
            self.in_flight -= 1

    def file_requests(self):
        return [url for url, _ in self.calls if url.endswith("/files")]

    def list_requests(self):
        return [url for url, _ in self.calls if url.endswith("/pulls")]

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patched(client):
    """Patch the token and hand the endpoint our client."""
    return (
        patch("app.GITHUB_TOKEN", "fake-token"),
        patch("httpx.AsyncClient", client),
    )


async def _get(url):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get(url)


class TestIsImagePath:
    """One definition of "image", shared by the viewer and the repo page."""

    def test_known_extensions(self):
        assert _is_image_path("a/b/shot.png")
        assert _is_image_path("SHOT.PNG")
        assert _is_image_path("photos/hero.jpg")
        assert _is_image_path("baseline.bmp")

    def test_non_images(self):
        assert not _is_image_path("src/main.py")
        assert not _is_image_path("README")
        assert not _is_image_path("notes.txt")

    def test_matches_the_extension_table(self):
        """It answers for the table, not for a list written out beside it."""
        for ext in IMAGE_EXTENSIONS:
            assert _is_image_path(f"dir/file{ext}")


class TestRepoPage:
    @pytest.mark.asyncio
    async def test_repo_page_returns_html(self):
        resp = await _get("/owner/repo")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_serves_the_repo_page_not_the_viewer(self):
        """Both routes return HTML, so status and type cannot tell them apart.

        Without this, pointing the new route at index.html would pass every
        other assertion here.
        """
        repo_page = (await _get("/owner/repo")).text
        viewer = (await _get("/owner/repo/pr/1")).text
        assert "/static/repo.js" in repo_page
        assert "/static/repo.js" not in viewer

    @pytest.mark.asyncio
    async def test_the_page_script_is_served(self):
        resp = await _get("/static/repo.js")
        assert resp.status_code == 200
        assert "verdictFor" in resp.text


class TestShortRepoRedirect:
    @pytest.mark.asyncio
    async def test_redirect_by_repo_name(self):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "items": [{"name": "visual-review", "owner": {"login": "widdowson"}}]
        }
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=mock_resp)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)

        with patch("app.GITHUB_TOKEN", "fake-token"), patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value = instance
            resp = await _get("/visual-review")

        assert resp.status_code == 302
        assert resp.headers["location"] == "/widdowson/visual-review"

    @pytest.mark.asyncio
    async def test_ambiguous_name_offers_repo_pages(self):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "items": [
                {"name": "app", "owner": {"login": "alice"}},
                {"name": "app", "owner": {"login": "bob"}},
            ]
        }
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=mock_resp)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)

        with patch("app.GITHUB_TOKEN", "fake-token"), patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value = instance
            resp = await _get("/app")

        assert resp.status_code == 300
        urls = [m["url"] for m in resp.json()["matches"]]
        assert urls == ["/alice/app", "/bob/app"]

    @pytest.mark.asyncio
    async def test_unknown_identifier_is_404(self):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"items": []}
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=mock_resp)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)

        with patch("app.GITHUB_TOKEN", "fake-token"), patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value = instance
            resp = await _get("/nosuchrepo")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_favicon_costs_no_github_request(self):
        """A browser asks for /favicon.ico on its own.

        Resolving it would spend a GitHub search — and cache the miss for an
        hour — on a word no user typed. Asserting the 404 alone would not
        catch that: a failed search 404s too.
        """
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=MagicMock(status_code=200))
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)

        with patch("app.GITHUB_TOKEN", "fake-token"), patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value = instance
            resp = await _get("/favicon.ico")

        assert resp.status_code == 404
        assert instance.get.await_count == 0


class TestRepoPulls:
    @pytest.mark.asyncio
    async def test_no_token(self):
        resp = await _get("/api/owner/repo/pulls")
        assert resp.json()["pulls"] == []
        assert "GITHUB_TOKEN" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_counts_images_per_pull_request(self):
        client = _RepoClient(
            [[_pull(10, title="Baselines", labels=["lgtm"]), _pull(11, draft=True)]],
            files_by_number={
                10: [
                    {"filename": "tests/visual/a.png"},
                    {"filename": "tests/visual/b.PNG"},
                    {"filename": "app.py"},
                ],
                11: [{"filename": "README.md"}],
            },
        )
        token, http = _patched(client)
        with token, http:
            resp = await _get("/api/owner/repo/pulls")

        data = resp.json()
        assert data["repo"] == "owner/repo"
        assert data["probed"] is True
        assert data["truncated"] is False
        rows = {row["number"]: row for row in data["pulls"]}
        assert rows[10]["images"] == 2
        assert rows[10]["image_error"] is None
        assert rows[10]["title"] == "Baselines"
        assert rows[10]["labels"] == ["lgtm"]
        assert rows[11]["images"] == 0
        assert rows[11]["draft"] is True
        # One list request, one per PR, and nothing else.
        assert len(client.list_requests()) == 1
        assert len(client.file_requests()) == 2

    @pytest.mark.asyncio
    async def test_probe_off_is_one_request_and_no_counts(self):
        """The cheap first request the page makes: rows now, counts after.

        ``images`` stays None rather than 0, because the page renders 0 as
        "no images" and nothing has been counted yet.
        """
        client = _RepoClient([[_pull(1), _pull(2)]], files_by_number={1: [{"filename": "a.png"}]})
        token, http = _patched(client)
        with token, http:
            resp = await _get("/api/owner/repo/pulls?probe=0")

        data = resp.json()
        assert data["probed"] is False
        assert [row["images"] for row in data["pulls"]] == [None, None]
        assert client.file_requests() == []
        assert len(client.list_requests()) == 1

    @pytest.mark.asyncio
    async def test_a_failed_probe_is_not_an_empty_pr(self):
        """The one wrong answer this page can give.

        A PR whose file list could not be fetched must not come back as
        ``images: 0`` — that is the page telling someone a PR is empty when
        nobody checked.
        """
        client = _RepoClient(
            [[_pull(1), _pull(2)]],
            files_by_number={2: [{"filename": "a.png"}]},
            files_status={1: 500},
        )
        token, http = _patched(client)
        with token, http:
            resp = await _get("/api/owner/repo/pulls")

        rows = {row["number"]: row for row in resp.json()["pulls"]}
        assert rows[1]["images"] is None
        assert "500" in rows[1]["image_error"]
        # The failure is contained: the other PR still gets its count.
        assert rows[2]["images"] == 1
        assert rows[2]["image_error"] is None

    @pytest.mark.asyncio
    async def test_a_failed_probe_is_not_cached(self):
        """A retry has to be able to succeed.

        Caching the error would pin "could not be checked" to that head SHA
        for the whole TTL, so a transient 500 would outlive itself by a
        quarter of an hour.
        """
        client = _RepoClient([[_pull(1)]], files_by_number={1: [{"filename": "a.png"}]},
                             files_status={1: 500})
        token, http = _patched(client)
        with token, http:
            first = await _get("/api/owner/repo/pulls")
            client.files_status = {}
            second = await _get("/api/owner/repo/pulls")

        assert first.json()["pulls"][0]["images"] is None
        assert second.json()["pulls"][0]["images"] == 1

    @pytest.mark.asyncio
    async def test_a_second_visit_asks_github_for_nothing(self):
        """Both caches, checked by their effect rather than by their keys."""
        client = _RepoClient([[_pull(1), _pull(2)]],
                             files_by_number={1: [{"filename": "a.png"}], 2: []})
        token, http = _patched(client)
        with token, http:
            await _get("/api/owner/repo/pulls")
            calls_after_first = len(client.calls)
            resp = await _get("/api/owner/repo/pulls")

        assert len(client.calls) == calls_after_first
        assert [row["images"] for row in resp.json()["pulls"]] == [1, 0]

    @pytest.mark.asyncio
    async def test_counts_are_not_written_into_the_list_cache(self):
        """The list cache is keyed on the repo alone.

        A count written into it would outlive the head SHA it was measured
        against, and would then be served for a PR that has since been pushed
        to. The summaries have their own per-SHA cache for exactly that.
        """
        client = _RepoClient([[_pull(1)]], files_by_number={1: [{"filename": "a.png"}]})
        token, http = _patched(client)
        with token, http:
            await _get("/api/owner/repo/pulls")
            resp = await _get("/api/owner/repo/pulls?probe=0")

        assert resp.json()["pulls"][0]["images"] is None

    @pytest.mark.asyncio
    async def test_probe_fan_out_is_capped(self):
        """The burst GitHub sees is bounded by the semaphore, not by the repo.

        Drop the semaphore and this is 20 requests in flight at once.
        """
        pulls = [_pull(n) for n in range(1, 21)]
        client = _RepoClient([pulls], files_by_number={n: [] for n in range(1, 21)})
        token, http = _patched(client)
        with token, http:
            await _get("/api/owner/repo/pulls")

        assert len(client.file_requests()) == 20
        assert client.max_in_flight == _PR_PROBE_CONCURRENCY

    @pytest.mark.asyncio
    async def test_open_pull_requests_past_the_first_page(self):
        """More than 100 open PRs: every one of them is listed."""
        page1 = [_pull(n) for n in range(1, 101)]
        page2 = [_pull(n) for n in range(101, 121)]
        client = _RepoClient([page1, page2])
        token, http = _patched(client)
        with token, http:
            resp = await _get("/api/owner/repo/pulls?probe=0")

        data = resp.json()
        assert len(data["pulls"]) == 120
        assert data["truncated"] is False
        assert [page for url, page in client.calls if url.endswith("/pulls")] == [1, 2]

    @pytest.mark.asyncio
    async def test_a_capped_list_walk_says_so(self):
        """The walk stops at its page cap and the payload admits it."""
        pages = [[_pull(n) for n in range(i * 100, i * 100 + 100)]
                 for i in range(_OPEN_PULLS_MAX_PAGES)]
        client = _RepoClient(pages)
        token, http = _patched(client)
        with token, http:
            resp = await _get("/api/owner/repo/pulls?probe=0")

        data = resp.json()
        assert data["truncated"] is True
        assert len(data["pulls"]) == _OPEN_PULLS_MAX_PAGES * 100
        assert len(client.list_requests()) == _OPEN_PULLS_MAX_PAGES

    @pytest.mark.asyncio
    async def test_a_pull_requests_files_past_the_first_page_are_counted(self):
        """A PR's own file list pages too, so the count is not capped at 100."""
        files = [{"filename": f"shots/s{i:04d}.png"} for i in range(150)]
        files += [{"filename": f"src/f{i}.py"} for i in range(10)]
        client = _RepoClient([[_pull(1)]], files_by_number={1: files})
        token, http = _patched(client)
        with token, http:
            resp = await _get("/api/owner/repo/pulls")

        assert resp.json()["pulls"][0]["images"] == 150
        assert len(client.file_requests()) == 2

    @pytest.mark.asyncio
    async def test_list_failure_is_reported(self):
        client = _RepoClient([[_pull(1)]], list_status={1: 404})
        token, http = _patched(client)
        with token, http:
            resp = await _get("/api/owner/repo/pulls")

        data = resp.json()
        assert data["pulls"] == []
        assert "404" in data["error"]

    @pytest.mark.asyncio
    async def test_no_open_pull_requests(self):
        client = _RepoClient([[]])
        token, http = _patched(client)
        with token, http:
            resp = await _get("/api/owner/repo/pulls")

        assert resp.json()["pulls"] == []
        assert "error" not in resp.json()

    @pytest.mark.asyncio
    async def test_a_pull_request_with_more_files_than_the_walk_serves(self):
        """The file walk's own cap reaches the payload.

        30 full pages is where ``_gh_paginate`` stops, and the count it
        reports from there is a floor. Without this, passing ``truncated``
        through could be cut to a literal False with every other assertion
        here still green — the page reads that flag to decide between "40
        images" and "40+ images", and between "no images" and "too many
        files to check".
        """
        files = [{"filename": f"shots/s{i:05d}.png"} for i in range(_GH_PAGE_SIZE * 30)]
        client = _RepoClient([[_pull(1)]], files_by_number={1: files})
        token, http = _patched(client)
        with token, http:
            resp = await _get("/api/owner/repo/pulls")

        row = resp.json()["pulls"][0]
        assert row["images_truncated"] is True
        assert row["images"] == _GH_PAGE_SIZE * 30
        assert len(client.file_requests()) == 30

    @pytest.mark.asyncio
    async def test_a_body_that_is_not_a_list_is_an_error(self):
        """GitHub answering 200 with an object would otherwise be walked.

        ``items.extend`` over a dict extends with its *keys*, so an error
        object would arrive as a handful of files named "message" and
        "documentation_url" — a list the page would render as real.
        """
        client = _RepoClient([[_pull(1)]])
        client.pull_pages = [{"message": "Moved Permanently"}]
        token, http = _patched(client)
        with token, http:
            resp = await _get("/api/owner/repo/pulls")

        data = resp.json()
        assert data["pulls"] == []
        assert "dict" in data["error"]

    @pytest.mark.asyncio
    async def test_a_transport_failure_is_reported_not_raised(self):
        """A connection that never answers is an error message, not a 500."""
        class _Exploding:
            async def get(self, url, **kwargs):
                raise RuntimeError("connection reset")

            def __call__(self, *a, **kw):
                return self

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        with patch("app.GITHUB_TOKEN", "fake-token"), patch("httpx.AsyncClient", _Exploding()):
            resp = await _get("/api/owner/repo/pulls")

        assert resp.status_code == 200
        assert resp.json()["pulls"] == []
        assert "connection reset" in resp.json()["error"]
