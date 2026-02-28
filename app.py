"""Visual Review — a standalone GitHub PR image diff viewer.

Proxies PNG images from GitHub's API and serves a single-page app
for side-by-side, crossfade, swipe, and diff overlay comparisons.
"""

import base64
import logging
import os
import time
from typing import Any

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("visual-review")

app = FastAPI(title="Visual Review")

# CORS: allow crossOrigin='anonymous' image loads for canvas-based pixel diffing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# -- Configuration ------------------------------------------------------------
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# -- In-memory cache ----------------------------------------------------------
_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str, ttl: float) -> Any | None:
    if key in _cache:
        ts, val = _cache[key]
        if time.time() - ts < ttl:
            return val
    return None


def _cache_set(key: str, val: Any) -> None:
    _cache[key] = (time.time(), val)


# -- Helpers -------------------------------------------------------------------

def _gh_headers() -> dict[str, str]:
    """Return standard GitHub API headers."""
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


# -- Static files --------------------------------------------------------------
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/health")
async def health_check():
    """Simple liveness check."""
    return {"status": "ok"}


# -- Visual review SPA ---------------------------------------------------------

@app.get("/{owner}/{repo}/pr/{number}")
async def visual_review_page(owner: str, repo: str, number: int):
    """Serve the visual review SPA for any owner/repo/PR."""
    return FileResponse(os.path.join(static_dir, "index.html"))


# -- API endpoints -------------------------------------------------------------

@app.get("/api/{owner}/{repo}/pr/{number}/images")
async def pr_images(owner: str, repo: str, number: int):
    """List all changed PNG files in a PR."""
    github_repo = f"{owner}/{repo}"

    if not GITHUB_TOKEN:
        return JSONResponse(
            content={"error": "No GITHUB_TOKEN configured", "images": []},
            headers={"Cache-Control": "no-store"},
        )

    headers = _gh_headers()
    result = {"pr_number": number, "images": [], "base_ref": None, "head_ref": None}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            pr_resp = await client.get(
                f"https://api.github.com/repos/{github_repo}/pulls/{number}",
                headers=headers,
            )
            if pr_resp.status_code != 200:
                return JSONResponse(
                    content={"error": f"PR not found: HTTP {pr_resp.status_code}", "images": []},
                    headers={"Cache-Control": "no-store"},
                )

            pr_data = pr_resp.json()
            base_ref = pr_data["base"]["sha"]
            head_ref = pr_data["head"]["sha"]

            # Cache keyed on PR number + head SHA (invalidates on force-push)
            cache_key = f"pr_images:{github_repo}:{number}:{head_ref}"
            cached = _cache_get(cache_key, 120)
            if cached is not None:
                return JSONResponse(
                    content=cached,
                    headers={"Cache-Control": "no-store"},
                )

            result["base_ref"] = base_ref
            result["head_ref"] = head_ref
            result["base_label"] = pr_data["base"]["label"]
            result["head_label"] = pr_data["head"]["label"]
            result["pr_title"] = pr_data["title"]
            result["pr_url"] = pr_data["html_url"]

            # Compare API to find changed files
            compare_resp = await client.get(
                f"https://api.github.com/repos/{github_repo}/compare/{base_ref}...{head_ref}",
                headers=headers,
            )
            if compare_resp.status_code != 200:
                return JSONResponse(
                    content={"error": f"Compare failed: HTTP {compare_resp.status_code}", "images": []},
                    headers={"Cache-Control": "no-store"},
                )

            compare_data = compare_resp.json()
            files = compare_data.get("files", [])

            for f in files:
                filename = f.get("filename", "")
                if filename.lower().endswith(".png"):
                    result["images"].append({
                        "path": filename,
                        "status": f.get("status", "modified"),
                        "additions": f.get("additions", 0),
                        "deletions": f.get("deletions", 0),
                    })

    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "images": []},
            headers={"Cache-Control": "no-store"},
        )

    _cache_set(cache_key, result)
    return JSONResponse(
        content=result,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/{owner}/{repo}/pr/{number}/image")
async def pr_image(
    owner: str, repo: str, number: int,
    path: str = Query(...), ref: str = Query(...),
):
    """Proxy image content from a specific git ref via GitHub contents API."""
    github_repo = f"{owner}/{repo}"

    if not GITHUB_TOKEN:
        logger.error("pr_image: no GITHUB_TOKEN configured")
        return Response(content=b"No GitHub token", status_code=500)

    headers = _gh_headers()
    img_headers = {"Cache-Control": "public, max-age=300"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{github_repo}/contents/{path}",
                headers=headers,
                params={"ref": ref},
            )
            if resp.status_code != 200:
                logger.warning(
                    "pr_image: GitHub API returned %d for path=%s ref=%s",
                    resp.status_code, path, ref[:12],
                )
                return Response(
                    content=f"GitHub API error: HTTP {resp.status_code}".encode(),
                    status_code=resp.status_code,
                )

            data = resp.json()

            # Case 1: Small file — base64 inline
            if data.get("encoding") == "base64":
                content = base64.b64decode(data["content"])
                return Response(
                    content=content,
                    media_type="image/png",
                    headers=img_headers,
                )

            # Case 2: Large file — use Git Blob API
            file_sha = data.get("sha")
            if file_sha:
                blob_resp = await client.get(
                    f"https://api.github.com/repos/{github_repo}/git/blobs/{file_sha}",
                    headers=headers,
                )
                if blob_resp.status_code == 200:
                    blob_data = blob_resp.json()
                    if blob_data.get("encoding") == "base64":
                        content = base64.b64decode(blob_data["content"])
                        return Response(
                            content=content,
                            media_type="image/png",
                            headers=img_headers,
                        )

            # Case 3: Fallback — try download_url
            download_url = data.get("download_url")
            if download_url:
                logger.info(
                    "pr_image: falling back to download_url for path=%s (size=%s)",
                    path, data.get("size"),
                )
                img_resp = await client.get(download_url, follow_redirects=True)
                if img_resp.status_code == 200:
                    return Response(
                        content=img_resp.content,
                        media_type="image/png",
                        headers=img_headers,
                    )

            logger.warning(
                "pr_image: could not retrieve image path=%s ref=%s encoding=%s sha=%s",
                path, ref[:12], data.get("encoding"), data.get("sha"),
            )
            return Response(content=b"Could not retrieve image", status_code=404)

    except Exception as e:
        logger.exception("pr_image: unexpected error for path=%s ref=%s", path, ref[:12])
        return Response(content=str(e).encode(), status_code=500)


@app.get("/api/{owner}/{repo}/pr/{number}/comments")
async def pr_comments(
    owner: str, repo: str, number: int,
    path: str = Query(...),
):
    """Fetch per-file review comments for a specific file path in a PR."""
    github_repo = f"{owner}/{repo}"

    if not GITHUB_TOKEN:
        return {"error": "No GITHUB_TOKEN configured", "comments": []}

    headers = _gh_headers()

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{github_repo}/pulls/{number}/comments",
                headers=headers,
                params={"per_page": 100},
            )
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}", "comments": []}

            all_comments = resp.json()
            file_comments = []
            for c in all_comments:
                if c.get("path") == path:
                    file_comments.append({
                        "id": c["id"],
                        "body": c["body"],
                        "user": c["user"]["login"],
                        "created_at": c["created_at"],
                        "updated_at": c.get("updated_at"),
                        "html_url": c.get("html_url", ""),
                    })

            return {"comments": file_comments}

    except Exception as e:
        return {"error": str(e), "comments": []}


@app.get("/api/{owner}/{repo}/pr/{number}/comment-counts")
async def pr_comment_counts(owner: str, repo: str, number: int):
    """Return comment counts grouped by file path for a PR."""
    github_repo = f"{owner}/{repo}"

    if not GITHUB_TOKEN:
        return {"counts": {}}

    headers = _gh_headers()

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{github_repo}/pulls/{number}/comments",
                headers=headers,
                params={"per_page": 100},
            )
            if resp.status_code != 200:
                return {"counts": {}}

            counts: dict[str, int] = {}
            for c in resp.json():
                p = c.get("path", "")
                if p:
                    counts[p] = counts.get(p, 0) + 1

            return {"counts": counts}

    except Exception:
        return {"counts": {}}


@app.post("/api/{owner}/{repo}/pr/{number}/comments")
async def post_pr_comment(owner: str, repo: str, number: int, request: Request):
    """Post a new per-file review comment on a PR."""
    github_repo = f"{owner}/{repo}"

    if not GITHUB_TOKEN:
        return {"error": "No GITHUB_TOKEN configured"}

    try:
        payload = await request.json()
    except Exception:
        return {"error": "Invalid JSON body"}

    file_path = payload.get("path", "").strip()
    comment_body = payload.get("body", "").strip()

    if not file_path or not comment_body:
        return {"error": "Both 'path' and 'body' are required"}

    headers = _gh_headers()

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            pr_resp = await client.get(
                f"https://api.github.com/repos/{github_repo}/pulls/{number}",
                headers=headers,
            )
            if pr_resp.status_code != 200:
                return {"error": f"PR not found: HTTP {pr_resp.status_code}"}

            pr_data = pr_resp.json()
            head_sha = pr_data["head"]["sha"]

            comment_resp = await client.post(
                f"https://api.github.com/repos/{github_repo}/pulls/{number}/comments",
                headers=headers,
                json={
                    "body": comment_body,
                    "commit_id": head_sha,
                    "path": file_path,
                    "subject_type": "file",
                },
            )

            if comment_resp.status_code in (200, 201):
                c = comment_resp.json()
                return {
                    "ok": True,
                    "comment": {
                        "id": c["id"],
                        "body": c["body"],
                        "user": c["user"]["login"],
                        "created_at": c["created_at"],
                        "html_url": c.get("html_url", ""),
                    },
                }
            else:
                err_body = comment_resp.text
                return {"error": f"GitHub API error: HTTP {comment_resp.status_code}: {err_body}"}

    except Exception as e:
        return {"error": str(e)}


# -- Root redirect -------------------------------------------------------------

@app.get("/")
async def root():
    """Show a simple landing page."""
    return JSONResponse(content={
        "app": "Visual Review",
        "usage": "Navigate to /{owner}/{repo}/pr/{number} to review a PR's visual changes.",
    })
