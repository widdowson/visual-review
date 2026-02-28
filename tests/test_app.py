"""Tests for visual-review app — FastAPI endpoint tests."""

import base64
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure the app module is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app, _cache


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
            "base": {"sha": "aaa", "label": "main"},
            "head": {"sha": "bbb", "label": "feature"},
            "title": "Test PR",
            "html_url": "http://gh/pr/1",
        }

        mock_compare_resp = MagicMock()
        mock_compare_resp.status_code = 200
        mock_compare_resp.json.return_value = {
            "files": [
                {"filename": "tests/screenshots/baseline/test.png", "status": "modified"},
                {"filename": "src/main.py", "status": "modified"},
                {"filename": "tests/screenshots/baseline/new.PNG", "status": "added"},
            ]
        }

        async def mock_get(url, **kwargs):
            if "/pulls/" in url:
                return mock_pr_resp
            if "/compare/" in url:
                return mock_compare_resp
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
        # Only .png files should be included (2 out of 3)
        assert len(data["images"]) == 2
        assert data["images"][0]["path"] == "tests/screenshots/baseline/test.png"
        assert data["images"][1]["path"] == "tests/screenshots/baseline/new.PNG"

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
