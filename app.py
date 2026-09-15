"""Visual Review — a standalone GitHub PR image diff viewer.

Proxies image files from GitHub's API and serves a single-page app for
side-by-side, crossfade, swipe, and diff overlay comparisons.
"""

import base64
import json
import logging
import os
import re
import time
from typing import Any

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
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


_ext_file = os.path.join(os.path.dirname(__file__), "image_extensions.json")
with open(_ext_file) as _f:
    _EXT_MIME: dict[str, str] = json.load(_f)

IMAGE_EXTENSIONS = _EXT_MIME.keys()


def _mime_for_path(path: str) -> str:
    """Return the MIME type for an image path based on its extension."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _EXT_MIME.get(f".{ext}", "image/png")


# -- Helpers -------------------------------------------------------------------

_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")


def _image_cache_control(ref: str) -> str:
    """Cache-Control for a proxied image.

    A full commit SHA addresses immutable content, so the browser never needs
    to ask for it twice — which is what makes moving back and forth between
    files, and the SPA's speculative prefetch, cost nothing after the first
    fetch. Any other ref (a branch name someone typed into the URL) can move,
    so it keeps a short TTL.

    ``private`` rather than ``public``: the whole benefit is browser-side, and
    these bytes may come from a private repository, so there is nothing to gain
    from letting an intermediary hold them for a year. ``fullmatch`` rather
    than ``match`` because ``$`` also matches before a trailing newline.
    """
    if _FULL_SHA_RE.fullmatch(ref or ""):
        return "private, max-age=31536000, immutable"
    return "private, max-age=300"


def _gh_headers() -> dict[str, str]:
    """Return standard GitHub API headers."""
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


# -- Repo resolver for short URLs ---------------------------------------------

def _base36_decode(s: str) -> int | None:
    """Decode a base36 string to an integer, or None if invalid."""
    try:
        return int(s, 36)
    except ValueError:
        return None


def _base36_encode(n: int) -> str:
    """Encode a non-negative integer as a base36 string."""
    if n < 0:
        raise ValueError("Cannot base36-encode negative numbers")
    if n == 0:
        return "0"
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = []
    while n:
        result.append(chars[n % 36])
        n //= 36
    return "".join(reversed(result))


async def _lookup_repo_by_id(client: httpx.AsyncClient, repo_id: int, headers: dict) -> list[tuple[str, str]]:
    """Look up a repo by numeric GitHub ID. Returns 0 or 1 matches."""
    resp = await client.get(
        f"https://api.github.com/repositories/{repo_id}",
        headers=headers,
    )
    if resp.status_code == 200:
        data = resp.json()
        return [(data["owner"]["login"], data["name"])]
    return []


async def _resolve_repo(identifier: str) -> list[tuple[str, str]]:
    """Resolve a short identifier to (owner, repo) pairs.

    Resolution order:
    - Numeric (all digits): look up via GET /repositories/{id}
    - Otherwise: search by exact repo name via GitHub search API
    - If name search finds nothing: try base36 decode → repo ID lookup

    Base36 gives compact repo IDs (e.g., 1125541223 → "im495z").

    Returns a list of (owner, repo) tuples. Empty list means no match.
    Results are cached for 1 hour.
    """
    cache_key = f"resolve_repo:{identifier}"
    cached = _cache_get(cache_key, 3600)
    if cached is not None:
        return cached

    if not GITHUB_TOKEN:
        return []

    # Validate identifier: only alphanumeric, hyphens, underscores, dots
    # (matches GitHub repo name rules + base36 charset)
    if not all(c.isalnum() or c in '-_.' for c in identifier):
        return []

    headers = _gh_headers()
    matches: list[tuple[str, str]] = []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if identifier.isdigit():
                # Pure numeric — decimal repo ID
                matches = await _lookup_repo_by_id(client, int(identifier), headers)
            else:
                # Try as repo name first — quote identifier to prevent search qualifier injection
                resp = await client.get(
                    "https://api.github.com/search/repositories",
                    headers=headers,
                    params={"q": f'"{identifier}" in:name', "per_page": 10},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    matches = [
                        (r["owner"]["login"], r["name"])
                        for r in data.get("items", [])
                        if r["name"].lower() == identifier.lower()
                    ]

                # If no name match, try base36 decode → repo ID lookup
                if not matches:
                    repo_id = _base36_decode(identifier)
                    if repo_id is not None:
                        matches = await _lookup_repo_by_id(client, repo_id, headers)
    except Exception:
        return []

    _cache_set(cache_key, matches)
    return matches


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


@app.get("/{identifier}/pr/{number}")
async def short_url_redirect(identifier: str, number: int):
    """Resolve a short URL and 302-redirect to the canonical path.

    Supports:
    - /{repo_name}/pr/{number} — resolve owner by searching for repo name
    - /{repo_id}/pr/{number} — resolve owner + name by numeric GitHub repo ID
    """
    matches = await _resolve_repo(identifier)

    if len(matches) == 1:
        owner, repo = matches[0]
        return RedirectResponse(url=f"/{owner}/{repo}/pr/{number}", status_code=302)

    if len(matches) > 1:
        return JSONResponse(
            status_code=300,
            content={
                "error": "Ambiguous repository name",
                "identifier": identifier,
                "matches": [
                    {"owner": owner, "repo": repo, "url": f"/{owner}/{repo}/pr/{number}"}
                    for owner, repo in matches
                ],
            },
        )

    return JSONResponse(
        status_code=404,
        content={"error": f"Repository not found: {identifier}"},
    )


# -- API endpoints -------------------------------------------------------------

@app.get("/api/{owner}/{repo}/pr/{number}/images")
async def pr_images(owner: str, repo: str, number: int):
    """List all changed image files in a PR."""
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
            result["repo_id"] = pr_data["base"]["repo"]["id"]

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
                ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
                if f".{ext}" in IMAGE_EXTENSIONS:
                    entry = {
                        "path": filename,
                        "status": f.get("status", "modified"),
                        "additions": f.get("additions", 0),
                        "deletions": f.get("deletions", 0),
                    }
                    if f.get("status") == "renamed" and f.get("previous_filename"):
                        entry["previous_filename"] = f["previous_filename"]
                    result["images"].append(entry)

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


# -- Client-disconnect handling -----------------------------------------------
#
# The SPA aborts an image load the moment the user navigates away from a file,
# so a fast scroll leaves requests in flight that nobody is waiting for. Each
# one still costs a GitHub API call against a rate limit shared by everyone on
# the deployment, and a proxied image can need up to three.
#
# Whether a check is worth making depends on the server reporting the abort
# while there is still a call left to skip, so that was measured before any of
# this was built, against this endpoint under real uvicorn 0.42 with a stubbed
# upstream and raw sockets aborted mid-flight.
#
# Detection is prompt. On both the httptools and the h11 implementation, and
# for an abort delivered as a FIN, as an RST and as a half-close,
# ``is_disconnected()`` returned True at the first poll after the abort landed
# — six runs, all six inside one 5ms poll, which is the harness's resolution
# rather than a latency figure.
#
# The yield, against a control build with the checks removed: 50 requests one
# per connection, which is the only shape a browser produces, all aborted
# while the contents call was in flight — 100 upstream calls became 50. Every
# second call was skipped.
#
# What no check can do is refund a call already sent, which is why there is
# deliberately no check before the *first* upstream call. One was written and
# then removed on the measurement: it fired in none of 900 aborted requests
# across every timing and concurrency tried, including with the event loop
# blocked for 1.5s while requests were both sent and aborted, and it cannot
# fire on the case that looked most promising. Only a request dispatched from
# behind another one on the same connection could begin already disconnected,
# and uvicorn never dispatches one: ``on_response_complete`` opens with ``if
# self.transport.is_closing(): return`` in both implementations, so a queued
# pipelined cycle on a closing connection is dropped rather than run. That is
# read off the source rather than inferred from the wire, deliberately — an
# RST discards the unread receive buffer instead of queueing behind it, so
# how many of those requests were ever parsed varies with the abort shape,
# and the conclusion should not rest on that.
#
# Pipelining does save calls here, and it is worth knowing that the saving is
# not a check firing, because the obvious reading of the call counts is wrong.
# Four pipelined and aborted at 50ms cost 4 upstream calls with no checks and
# 2 with them — but a third arm that polls ``is_disconnected()`` and *throws
# the verdict away* also costs 2, with both checks evaluating False. The poll
# itself is what does it: it calls ``receive()``, which resumes reading, which
# lets uvicorn notice the pending EOF and abandon the queued request.
#
# Either surviving check's poll alone reproduces that, so the removed one
# bought nothing on a request that reaches a check. It is not nothing on one
# that does not: an inline-content request returns from case 1 having polled
# zero times, measured, where the removed check polled on every request — so
# four pipelined small files cost 2 upstream calls now against 1 before.
# Small files are the common case in an image diff, so that is a real loss
# and not a rounding error. It does not change the removal: this saving is a
# side effect of polling rather than anything the check was for, a browser
# does not pipeline, and a poll kept solely for its side effect on a shape we
# never serve is a worse thing to own than the loss.
#
# Unverified from here: in production the browser talks to Cloud Run's front
# end, which talks HTTP/1.1 to this container. Whether it closes that backend
# connection when the client cancels decides whether any of this fires in the
# deployment. The checks are inert, not harmful, if it does not.

CLIENT_GONE_STATUS = 499


def _client_gone_response() -> Response:
    return Response(content=b"", status_code=CLIENT_GONE_STATUS)


async def _client_gone(request: Request, where: str, path: str) -> bool:
    """True when the client has disconnected, logging where we noticed.

    ``where`` names the upstream call this check is about to skip, because
    that is the only thing the log line can usefully say: the two call sites
    save different amounts and are worth telling apart.
    """
    if not await request.is_disconnected():
        return False
    logger.info("pr_image: client gone before %s, skipping it for path=%s", where, path)
    return True


@app.get("/api/{owner}/{repo}/pr/{number}/image")
async def pr_image(
    owner: str, repo: str, number: int, request: Request,
    path: str = Query(...), ref: str = Query(...),
):
    """Proxy image content from a specific git ref via GitHub contents API."""
    github_repo = f"{owner}/{repo}"

    if not GITHUB_TOKEN:
        logger.error("pr_image: no GITHUB_TOKEN configured")
        return Response(content=b"No GitHub token", status_code=500)

    headers = _gh_headers()
    img_headers = {"Cache-Control": _image_cache_control(ref)}
    mime = _mime_for_path(path)

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
                    media_type=mime,
                    headers=img_headers,
                )

            # Everything from here needs a further upstream call, and an
            # abort issued while the contents call was in flight lands exactly
            # here. That first call is spent either way; the ones that
            # transfer the bytes are not. Both checks sit below case 1 on
            # purpose — a small file's bytes came back with the contents call,
            # so there is nothing left to save by not returning them — and
            # each sits immediately before a single call, so the call it
            # skips is the one its log line names.

            # Case 2: Large file — use Git Blob API
            file_sha = data.get("sha")
            if file_sha:
                if await _client_gone(request, "the blob call", path):
                    return _client_gone_response()
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
                            media_type=mime,
                            headers=img_headers,
                        )

            # Case 3: Fallback — try download_url
            download_url = data.get("download_url")
            if download_url:
                if await _client_gone(request, "the download_url fetch", path):
                    return _client_gone_response()
                logger.info(
                    "pr_image: falling back to download_url for path=%s (size=%s)",
                    path, data.get("size"),
                )
                img_resp = await client.get(download_url, follow_redirects=True)
                if img_resp.status_code == 200:
                    return Response(
                        content=img_resp.content,
                        media_type=mime,
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


@app.get("/api/{owner}/{repo}/pr/{number}/checks")
async def pr_checks(owner: str, repo: str, number: int):
    """Return combined CI check status for a PR's head commit."""
    github_repo = f"{owner}/{repo}"

    if not GITHUB_TOKEN:
        return {"error": "No GITHUB_TOKEN configured"}

    headers = _gh_headers()

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Get the PR to find the head SHA
            pr_resp = await client.get(
                f"https://api.github.com/repos/{github_repo}/pulls/{number}",
                headers=headers,
            )
            if pr_resp.status_code != 200:
                return {"error": f"PR not found: HTTP {pr_resp.status_code}"}

            head_sha = pr_resp.json()["head"]["sha"]

            cache_key = f"pr_checks:{github_repo}:{number}:{head_sha}"
            cached = _cache_get(cache_key, 60)
            if cached is not None:
                return cached

            # Fetch check runs (GitHub Actions, etc.)
            checks_resp = await client.get(
                f"https://api.github.com/repos/{github_repo}/commits/{head_sha}/check-runs",
                headers=headers,
                params={"per_page": 100},
            )

            runs = []
            if checks_resp.status_code == 200:
                for r in checks_resp.json().get("check_runs", []):
                    runs.append({
                        "name": r["name"],
                        "status": r["status"],
                        "conclusion": r.get("conclusion"),
                        "html_url": r.get("html_url", ""),
                    })

            # Derive overall state
            if not runs:
                overall = "none"
            elif all(r["conclusion"] == "success" for r in runs):
                overall = "success"
            elif any(r["conclusion"] in ("failure", "timed_out", "action_required") for r in runs):
                overall = "failure"
            elif any(r["status"] in ("queued", "in_progress") for r in runs):
                overall = "pending"
            else:
                overall = "unknown"

            result = {"overall": overall, "runs": runs, "sha": head_sha[:8]}
            _cache_set(cache_key, result)
            return result

    except Exception as e:
        return {"error": str(e)}


# -- Root redirect -------------------------------------------------------------

@app.get("/")
async def root():
    """Show a simple landing page."""
    return JSONResponse(content={
        "app": "Visual Review",
        "usage": "Navigate to /{owner}/{repo}/pr/{number} to review a PR's visual changes.",
        "short_urls": "Also supports /{repo_name}/pr/{number}, /{repo_id}/pr/{number}, and /{base36_id}/pr/{number}.",
    })
