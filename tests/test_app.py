"""Tests for visual-review app — FastAPI endpoint tests."""

import base64
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure the app module is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app, _cache, _resolve_repo, _base36_decode, _base36_encode, _mime_for_path, _image_cache_control, _is_safe_image_path, _EXT_MIME, IMAGE_EXTENSIONS, _gh_paginate, _GitHubError, _GH_PAGE_SIZE, _GH_MAX_PAGES


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


# -- Supported extensions endpoint --------------------------------------------

class TestExtensionsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_the_servers_own_list(self):
        """The endpoint answers the same list the server matches with.

        Asserted against ``_EXT_MIME`` rather than a literal, so adding a
        format to image_extensions.json does not need this test edited. Note
        what it does *not* establish: ``_EXT_MIME`` is the constant the
        endpoint itself reads, so this pins the value the two share and not
        where the answer came from. An endpoint carrying a frozen copy of
        today's list passes here. ``test_serves_whatever_the_file_says``
        below is what rules that out.
        """
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/extensions")

        assert resp.status_code == 200
        assert resp.json() == {"extensions": list(_EXT_MIME.keys())}

    @pytest.mark.asyncio
    async def test_serves_whatever_the_file_says(self):
        """The answer is derived, not a second copy of the list.

        This is the one defect #13 exists to remove, so it needs a case that
        can see it: reintroducing the duplication on the server — a literal
        list in the handler instead of ``IMAGE_EXTENSIONS`` — leaves every
        other case in this class green, and the next format added to
        image_extensions.json silently stops being served.

        ``IMAGE_EXTENSIONS`` is ``_EXT_MIME.keys()``, a live view, so adding a
        key to the dict reaches the response with no production change.
        """
        with patch.dict("app._EXT_MIME", {".webp": "image/webp"}):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/extensions")

        assert ".webp" in resp.json()["extensions"]

    @pytest.mark.asyncio
    async def test_every_entry_is_a_lowercase_dotted_extension(self):
        """The shape the browser extension validates against before adopting it."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/extensions")

        extensions = resp.json()["extensions"]
        assert extensions, "an empty list is rejected by the client, so never serve one"
        for ext in extensions:
            assert isinstance(ext, str)
            assert ext.startswith("."), ext
            assert len(ext) >= 2, ext
            assert ext == ext.lower(), ext

    @pytest.mark.asyncio
    async def test_needs_no_github_token(self):
        """Every other API endpoint degrades without a token; this one must not."""
        with patch("app.GITHUB_TOKEN", ""):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/extensions")

        assert resp.status_code == 200
        assert resp.json()["extensions"] == list(_EXT_MIME.keys())

    @pytest.mark.asyncio
    async def test_is_publicly_cacheable(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/extensions")

        cache_control = resp.headers.get("cache-control", "")
        assert "public" in cache_control, cache_control
        # The value, not just its presence. The docstring on the endpoint
        # gives this hour a job — it is the only thing bounding the request
        # rate of a client whose localStorage never holds — so it can no more
        # move unnoticed than the client's own 5s budget can.
        assert "max-age=3600" in cache_control, cache_control

    @pytest.mark.asyncio
    async def test_answers_cross_origin(self):
        """Load-bearing, not incidental.

        The browser extension reads this from a content script running on
        github.com. A Manifest V3 content script's fetch carries the page's
        origin and is subject to CORS, and ``host_permissions`` cannot exempt
        it — that moved to the service worker in V3 — so losing this header
        makes the endpoint unreachable from the only client it has.
        """
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/extensions", headers={"Origin": "https://github.com"}
            )

        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "*"

    @pytest.mark.asyncio
    async def test_no_earlier_route_claims_a_two_segment_path(self):
        """A tripwire for a future route, not a pin on a live hazard.

        No dynamic route in app.py is two segments today — the short-URL
        resolver is ``/{identifier}/pr/{number}``, three — so this endpoint is
        unshadowable at any registration position, and moving it to the end of
        the file leaves this green. What it guards is the day someone adds a
        two-segment pattern above it: FastAPI matches in registration order, so
        such a route would claim ``/api/extensions`` and the extension would
        get that route's answer instead of the list.
        """
        route_paths = [getattr(r, "path", None) for r in app.routes]
        assert "/api/extensions" in route_paths

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/extensions")

        # A short-URL match would 404 or redirect rather than answer the list.
        assert resp.status_code == 200
        assert "extensions" in resp.json()


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

    # -- items_key: endpoints that wrap their array in an object (#22) -------

    @pytest.mark.asyncio
    async def test_items_key_reads_the_wrapped_array(self):
        """"List check runs" answers an object, not a bare array."""
        client = self._client([
            {"total_count": 3, "check_runs": [1, 2, 3]},
        ])
        items, truncated = await _gh_paginate(
            client, "http://gh/list", {}, items_key="check_runs")
        assert items == [1, 2, 3]
        assert truncated is False

    @pytest.mark.asyncio
    async def test_items_key_walks_every_page(self):
        """The page's own length ends the walk, not ``total_count``.

        ``total_count`` here says 1 while the endpoint serves a full page and
        then some. A walk that trusted the count would stop at page 1 and
        report a complete list.
        """
        client = self._client([
            {"total_count": 1, "check_runs": list(range(_GH_PAGE_SIZE))},
            {"total_count": 1, "check_runs": [999]},
        ])
        items, truncated = await _gh_paginate(
            client, "http://gh/list", {}, items_key="check_runs")
        assert len(items) == _GH_PAGE_SIZE + 1
        assert items[-1] == 999
        assert truncated is False
        assert client.requested == [1, 2]

    @pytest.mark.asyncio
    async def test_items_key_truncates_at_max_pages(self):
        client = self._client([
            {"total_count": 0, "check_runs": list(range(_GH_PAGE_SIZE))},
        ] * 3)
        items, truncated = await _gh_paginate(
            client, "http://gh/list", {}, items_key="check_runs", max_pages=2)
        assert len(items) == 2 * _GH_PAGE_SIZE
        assert truncated is True

    @pytest.mark.asyncio
    async def test_items_key_on_a_bare_array_raises(self):
        """Naming a key the body has no place for is a caller error."""
        client = self._client([[1, 2, 3]])
        with pytest.raises(_GitHubError) as exc:
            await _gh_paginate(client, "http://gh/list", {}, items_key="check_runs")
        assert exc.value.status_code is None
        assert "list" in str(exc.value)

    @pytest.mark.asyncio
    async def test_a_missing_items_key_raises(self):
        """An absent key must not read as "the list ended here".

        Defaulting to ``[]`` would end the walk on a short page and report a
        complete list, which is this issue's own bug one level down.
        """
        client = self._client([{"total_count": 0}])
        with pytest.raises(_GitHubError) as exc:
            await _gh_paginate(client, "http://gh/list", {}, items_key="check_runs")
        assert exc.value.status_code is None
        assert "check_runs" in str(exc.value)

    @pytest.mark.asyncio
    async def test_a_non_list_under_items_key_raises(self):
        client = self._client([{"check_runs": {"nope": 1}}])
        with pytest.raises(_GitHubError) as exc:
            await _gh_paginate(client, "http://gh/list", {}, items_key="check_runs")
        assert "dict" in str(exc.value)

    @pytest.mark.asyncio
    async def test_without_items_key_a_bare_array_is_still_read(self):
        """The default is unchanged: no key named, no object expected."""
        client = self._client([[1, 2]])
        items, truncated = await _gh_paginate(client, "http://gh/list", {})
        assert items == [1, 2]
        assert truncated is False


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


# -- Paginated review comments (#22) ------------------------------------------

def _comment_pages(total, path="test.png", start=0):
    """``total`` synthetic review comments on ``path``, split into full pages."""
    comments = [
        {
            "id": start + i,
            "body": f"comment {start + i}",
            "user": {"login": "reviewer"},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "path": path,
            "html_url": f"http://gh/comment/{start + i}",
        }
        for i in range(total)
    ]
    return [
        comments[i:i + _GH_PAGE_SIZE]
        for i in range(0, len(comments), _GH_PAGE_SIZE)
    ] or [[]]


def _comment_client(pages, status_by_page=None):
    """Mock an httpx.AsyncClient serving ``pages`` from pulls/{n}/comments.

    Page numbers are recorded on ``instance.requested_pages``: a walk that
    stops one page early and one that never stops both return plausible
    lists, so the requests are the only thing that tells them apart.
    """
    requested_pages = []

    async def mock_get(url, **kwargs):
        if url.endswith("/comments"):
            page = kwargs.get("params", {}).get("page")
            requested_pages.append(page)
            status = (status_by_page or {}).get(page, 200)
            resp = MagicMock()
            resp.status_code = status
            if status == 200:
                resp.json.return_value = pages[page - 1] if 1 <= page <= len(pages) else []
            return resp
        return MagicMock(status_code=404)

    instance = AsyncMock()
    instance.get = mock_get
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    instance.requested_pages = requested_pages
    return instance


class TestPrCommentsPagination:
    """#22: every review comment past the first 100 was dropped, silently."""

    @pytest.mark.asyncio
    async def test_a_comment_on_page_two_is_found(self):
        """The reported symptom: a file whose only comments are past page 1.

        Every one of the first 100 comments is on another file, so reading a
        single page returns an empty list for this path and says nothing —
        which is what the endpoint did.
        """
        page_one = _comment_pages(_GH_PAGE_SIZE, path="other.png")[0]
        page_two = _comment_pages(3, path="test.png", start=_GH_PAGE_SIZE)[0]
        instance = _comment_client([page_one, page_two])

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/comments?path=test.png")

        data = resp.json()
        assert [c["id"] for c in data["comments"]] == [100, 101, 102]
        assert data["truncated"] is False
        assert instance.requested_pages == [1, 2]

    @pytest.mark.asyncio
    async def test_all_comments_for_a_path_across_pages(self):
        """250 comments on one file come back whole, not just the first 100."""
        instance = _comment_client(_comment_pages(250))

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/comments?path=test.png")

        data = resp.json()
        assert len(data["comments"]) == 250
        assert data["comments"][-1]["body"] == "comment 249"
        assert instance.requested_pages == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_comments_truncated_reaches_the_response(self):
        """A capped walk is reported in the payload, not just internally.

        This drives the real ``_GH_MAX_PAGES``, which is a default argument
        bound at definition time — patching the module constant would not
        reach it. Without this case, ``"truncated": truncated`` could be cut
        to a literal ``False`` and every other comments test would still pass.
        """
        instance = _comment_client(_comment_pages(_GH_PAGE_SIZE * _GH_MAX_PAGES))

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/comments?path=test.png")

        data = resp.json()
        assert data["truncated"] is True
        assert len(data["comments"]) == _GH_PAGE_SIZE * _GH_MAX_PAGES
        assert instance.requested_pages == list(range(1, _GH_MAX_PAGES + 1))

    @pytest.mark.asyncio
    async def test_a_failing_page_is_an_error_not_a_short_list(self):
        """Page 2 failing must not be served as "these are all the comments"."""
        instance = _comment_client(_comment_pages(250), status_by_page={2: 502})

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/comments?path=test.png")

        data = resp.json()
        assert data["comments"] == []
        assert "502" in data["error"]
        assert instance.requested_pages == [1, 2]


class TestPrCommentCountsPagination:
    """The per-file badges undercounted for the same reason (#22)."""

    @pytest.mark.asyncio
    async def test_counts_span_every_page(self):
        """A count that stops at page 1 is wrong on both files here."""
        page_one = _comment_pages(_GH_PAGE_SIZE, path="test.png")[0]
        page_two = (
            _comment_pages(40, path="test.png", start=100)[0]
            + _comment_pages(10, path="other.png", start=200)[0]
        )
        instance = _comment_client([page_one, page_two])

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/comment-counts")

        data = resp.json()
        assert data["counts"] == {"test.png": 140, "other.png": 10}
        assert data["truncated"] is False
        assert instance.requested_pages == [1, 2]

    @pytest.mark.asyncio
    async def test_counts_report_truncation(self):
        instance = _comment_client(_comment_pages(_GH_PAGE_SIZE * _GH_MAX_PAGES))

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/comment-counts")

        data = resp.json()
        assert data["truncated"] is True
        assert data["counts"]["test.png"] == _GH_PAGE_SIZE * _GH_MAX_PAGES

    @pytest.mark.asyncio
    async def test_a_failing_page_yields_no_counts(self):
        """Unchanged from before the walk: this endpoint stays quiet on error.

        Badges are decoration, so a failure here drops them rather than
        breaking the page. What must not happen is the partial count from
        page 1 being served as if it were the whole tally.
        """
        instance = _comment_client(_comment_pages(250), status_by_page={2: 502})

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/comment-counts")

        assert resp.json()["counts"] == {}


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


class TestPrChecksPagination:
    """#22's aside: check-runs was read one page deep too.

    Less likely to bite than the comments endpoints — a head commit with more
    than 100 check runs is rare — but the stakes are higher per occurrence,
    because ``overall`` is derived from whatever came back. A partial list
    does not just shorten the report, it can invert it.
    """

    def _client(self, pages, status_by_page=None, sha="abc123def456"):
        requested_pages = []

        mock_pr_resp = MagicMock()
        mock_pr_resp.status_code = 200
        mock_pr_resp.json.return_value = {"head": {"sha": sha}}

        async def mock_get(url, **kwargs):
            if "/check-runs" in url:
                page = kwargs.get("params", {}).get("page")
                requested_pages.append(page)
                status = (status_by_page or {}).get(page, 200)
                resp = MagicMock()
                resp.status_code = status
                if status == 200:
                    runs = pages[page - 1] if 1 <= page <= len(pages) else []
                    resp.json.return_value = {"total_count": 0, "check_runs": runs}
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

    @staticmethod
    def _runs(count, conclusion="success", start=0):
        return [
            {
                "name": f"check {start + i}",
                "status": "completed",
                "conclusion": conclusion,
                "html_url": "",
            }
            for i in range(count)
        ]

    @pytest.mark.asyncio
    async def test_a_failure_on_page_two_flips_the_verdict(self):
        """The reason this endpoint is worth walking at all.

        100 passing checks then one failure. Reading page 1 alone reports
        ``success`` for a head commit whose CI is red — the one answer a
        reviewer acts on directly.
        """
        instance = self._client([
            self._runs(_GH_PAGE_SIZE),
            self._runs(1, conclusion="failure", start=_GH_PAGE_SIZE),
        ])

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/checks")

        data = resp.json()
        assert data["overall"] == "failure"
        assert len(data["runs"]) == _GH_PAGE_SIZE + 1
        assert data["truncated"] is False
        assert instance.requested_pages == [1, 2]

    @pytest.mark.asyncio
    async def test_checks_truncated_reaches_the_response(self):
        instance = self._client([self._runs(_GH_PAGE_SIZE)] * _GH_MAX_PAGES)

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/checks")

        data = resp.json()
        assert data["truncated"] is True
        assert len(data["runs"]) == _GH_PAGE_SIZE * _GH_MAX_PAGES
        assert instance.requested_pages == list(range(1, _GH_MAX_PAGES + 1))

    @pytest.mark.asyncio
    async def test_a_failing_page_leaves_no_runs(self):
        """Unchanged from the single-request version: a checks call that
        fails reports ``none`` rather than a partial verdict."""
        instance = self._client([self._runs(_GH_PAGE_SIZE)], status_by_page={2: 500})

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = instance
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/owner/repo/pr/1/checks")

        data = resp.json()
        assert data["overall"] == "none"
        assert data["runs"] == []


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


# -- Client disconnect ---------------------------------------------------------

class TestPrImageClientDisconnect:
    """A request the browser has cancelled must stop costing GitHub API calls.

    These drive the ASGI app directly rather than going through
    ``ASGITransport``. That transport does deliver ``http.disconnect``, but
    only after ``response_complete`` and only from behind an ``await`` that
    ``is_disconnected()``'s already-cancelled scope can never get past —
    measured: it reports False before the response and False after it. So the
    transport cannot express this case at all, which is equally why none of
    the other tests in this file change behaviour because of these checks.
    """

    @staticmethod
    def _scope(path: str = "test.png", ref: str = "a" * 40):
        from urllib.parse import urlencode
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/owner/repo/pr/1/image",
            "raw_path": b"/api/owner/repo/pr/1/image",
            "query_string": urlencode({"path": path, "ref": ref}).encode(),
            "root_path": "",
            "headers": [(b"host", b"test")],
            "client": ("1.2.3.4", 1234),
            "server": ("test", 80),
        }

    @classmethod
    async def _drive(cls, gone: str, *, contents: dict, blob: dict | None = None,
                     blob_status: int = 200, path: str = "test.png",
                     ref: str = "a" * 40):
        """Run pr_image against a stub GitHub, aborting when ``gone`` says.

        ``gone`` is "never", "at_start" (already gone when the handler ran),
        "during_contents" (gone while the contents call was in flight, which
        is where the SPA's aborts land) or "during_blob". The trigger is the
        stub upstream seeing that call, not a count of channel reads: a count
        would silently mean "the Nth check" and would move whenever a check
        was added or removed, which is exactly when these tests need to keep
        meaning what their names say.

        Returns (status, headers, body, upstream_urls). The URLs are the whole
        point — an assertion on the status alone would pass against a handler
        that made every call and threw the results away.
        """
        disconnected = {"now": gone == "at_start"}

        async def receive():
            if disconnected["now"]:
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": b"", "more_body": False}

        contents_resp = MagicMock()
        contents_resp.status_code = 200
        contents_resp.json.return_value = contents

        blob_resp = MagicMock()
        blob_resp.status_code = blob_status
        blob_resp.json.return_value = blob or {}

        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.content = b"downloaded-png-data"

        urls: list[str] = []

        async def mock_get(url, **kwargs):
            urls.append(url)
            if "/contents/" in url:
                if gone == "during_contents":
                    disconnected["now"] = True
                return contents_resp
            if "/git/blobs/" in url:
                if gone == "during_blob":
                    disconnected["now"] = True
                return blob_resp
            return download_resp

        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        with (
            patch("app.GITHUB_TOKEN", "fake-token"),
            patch("httpx.AsyncClient") as MockClient,
        ):
            instance = AsyncMock()
            instance.get = mock_get
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            await app(cls._scope(path=path, ref=ref), receive, send)

        start = next(m for m in sent if m["type"] == "http.response.start")
        body = b"".join(
            m.get("body", b"") for m in sent if m["type"] == "http.response.body"
        )
        headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
        return start["status"], headers, body, urls

    # The contents-call shapes, named so a failure says which case it was in
    # rather than which dict literal it was handed.
    _B64 = base64.b64encode(b"fake-png-data").decode()
    SMALL = {"encoding": "base64", "content": _B64}
    LARGE = {"sha": "deadbeef123", "size": 2_000_000,
             "download_url": "https://raw.githubusercontent.com/o/r/abc/test.png"}
    NO_SHA = {"size": 2_000_000,
              "download_url": "https://raw.githubusercontent.com/o/r/abc/test.png"}
    BLOB = {"encoding": "base64", "content": _B64}

    @staticmethod
    def _kinds(urls):
        return [
            "contents" if "/contents/" in u
            else "blob" if "/git/blobs/" in u
            else "download"
            for u in urls
        ]

    @pytest.mark.asyncio
    async def test_gone_during_the_contents_call_skips_the_blob_call(self):
        """The check with the most to save: the bytes fetch is never made.

        The contents call is spent — nothing can refund a call already sent —
        but the blob call that would have transferred the file is not made.
        """
        status, _, body, urls = await self._drive(
            "during_contents", contents=self.LARGE, blob=self.BLOB
        )
        assert status == 499
        assert self._kinds(urls) == ["contents"]
        assert body == b""

    @pytest.mark.asyncio
    async def test_gone_during_the_contents_call_skips_the_download_fallback(self):
        """Case 3 reached directly: no sha, so download_url is the next call."""
        status, _, _, urls = await self._drive(
            "during_contents", contents=self.NO_SHA
        )
        assert status == 499
        assert self._kinds(urls) == ["contents"]

    @pytest.mark.asyncio
    async def test_gone_during_the_blob_call_skips_the_download_fallback(self):
        """Case 3 reached *after* case 2, which the entry check cannot cover.

        A blob response that is not base64 falls through to download_url, so
        without a check of its own this request would spend a third upstream
        call and write a 200 into a socket that has gone.
        """
        status, _, body, urls = await self._drive(
            "during_blob", contents=self.LARGE, blob={"encoding": "utf-8"}
        )
        assert status == 499
        assert self._kinds(urls) == ["contents", "blob"]
        assert body == b""

    @pytest.mark.asyncio
    async def test_gone_during_the_blob_call_after_a_failed_blob_call(self):
        """The same fall-through, reached by a non-200 blob response."""
        status, _, _, urls = await self._drive(
            "during_blob", contents=self.LARGE, blob={}, blob_status=404
        )
        assert status == 499
        assert self._kinds(urls) == ["contents", "blob"]

    @pytest.mark.asyncio
    async def test_no_check_precedes_the_contents_call(self):
        """Deliberate, and measured: there is no check before the first call.

        One was written and removed — it fired in none of 900 aborted
        requests, and uvicorn will not dispatch a queued pipelined cycle on a
        closing connection, which was the only case that could have reached
        it. So a client already gone still spends the contents call, and is
        stopped at the next one. If this starts returning no upstream calls at
        all, a pre-contents check has come back and needs its own evidence.
        """
        status, _, _, urls = await self._drive(
            "at_start", contents=self.LARGE, blob=self.BLOB
        )
        assert status == 499
        assert self._kinds(urls) == ["contents"]

    @pytest.mark.asyncio
    async def test_small_file_already_in_hand_is_still_returned(self):
        """Deliberate: both checks sit below the inline-content case.

        A small file's bytes arrived with the contents call, so stopping short
        of returning them would save nothing. If this ever returns 499 a
        check has been moved above case 1.
        """
        status, headers, body, urls = await self._drive(
            "during_contents", contents=self.SMALL
        )
        assert status == 200
        assert body == b"fake-png-data"
        assert headers["cache-control"] == "private, max-age=31536000, immutable"
        assert self._kinds(urls) == ["contents"]

    @pytest.mark.asyncio
    async def test_connected_client_still_gets_every_call(self):
        """The checks must not fire on a client that is still there."""
        status, _, body, urls = await self._drive(
            "never", contents=self.LARGE, blob=self.BLOB
        )
        assert status == 200
        assert body == b"fake-png-data"
        assert self._kinds(urls) == ["contents", "blob"]

    @pytest.mark.asyncio
    async def test_connected_client_reaches_the_download_fallback(self):
        """The full three-call path, so the added check cannot be a blanket."""
        status, _, body, urls = await self._drive(
            "never", contents=self.LARGE, blob={"encoding": "utf-8"}
        )
        assert status == 200
        assert body == b"downloaded-png-data"
        assert self._kinds(urls) == ["contents", "blob", "download"]

    @pytest.mark.asyncio
    async def test_client_gone_response_is_not_cached(self):
        """A cached 499 would shadow the image on the next request for it."""
        cases = (
            ("during_contents", self.LARGE, self.BLOB),
            ("during_blob", self.LARGE, {"encoding": "utf-8"}),
        )
        for gone, contents, blob in cases:
            status, headers, _, _ = await self._drive(
                gone, contents=contents, blob=blob
            )
            assert status == 499, gone
            assert headers.get("cache-control") is None, gone

    @pytest.mark.asyncio
    async def test_each_check_logs_the_call_it_skipped(self, caplog):
        """The log line is the only thing that tells the checks apart.

        Without this, swapping the two label strings is a mutation the whole
        suite passes — so each one is pinned to the call it actually guards.
        """
        with caplog.at_level("INFO", logger="visual-review"):
            await self._drive("during_contents", contents=self.LARGE,
                              blob=self.BLOB)
        assert "client gone before the blob call" in caplog.text
        assert "download_url" not in caplog.text

        caplog.clear()
        with caplog.at_level("INFO", logger="visual-review"):
            await self._drive("during_blob", contents=self.LARGE,
                              blob={"encoding": "utf-8"})
        assert "client gone before the download_url fetch" in caplog.text
        assert "before the blob call" not in caplog.text

        # The third shape, and the one that makes this an invariant rather
        # than two spot checks: no sha, so the call skipped is the
        # download_url fetch even though the abort landed during the contents
        # call. A check placed at the case-2/3 entry point instead of
        # immediately before each call saves the same calls and mislabels
        # this one, which is a mutant the rest of the suite passes.
        caplog.clear()
        with caplog.at_level("INFO", logger="visual-review"):
            await self._drive("during_contents", contents=self.NO_SHA)
        assert "client gone before the download_url fetch" in caplog.text
        assert "before the blob call" not in caplog.text
