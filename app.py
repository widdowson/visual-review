"""Visual Review — a standalone GitHub PR image diff viewer.

Proxies image files from GitHub's API and serves a single-page app for
side-by-side, crossfade, swipe, and diff overlay comparisons.
"""

import asyncio
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


def _is_image_path(path: str) -> bool:
    """Whether a changed file is one this tool can show.

    Extension only, read from the same table that gives the MIME type. It is
    a function rather than the test written out at each call site so that the
    repo page's count of a PR's images and the viewer's list of them cannot
    come to disagree about what an image is — a page reporting "3 images" over
    a viewer showing two would be worse than no page at all.
    """
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return f".{ext}" in IMAGE_EXTENSIONS


def _is_safe_image_path(path: str) -> bool:
    """Whether ``path`` is a safe relative path to an image inside a repo.

    The ``path`` query parameter of :func:`pr_image` is interpolated straight
    into the GitHub contents URL, and httpx performs RFC 3986 dot-segment
    removal when it builds that URL — so ``../`` segments are collapsed
    *before* the request goes out, retargeting the call at another repository
    and returning its bytes under the deployment token (issue #37). Percent
    encoding does not survive query parsing to reach this check, so literal
    ``../`` segments are the working input; validate the decoded value.

    A path is safe only when every one of these holds:

    - it is non-empty and does not start with ``/`` (no absolute paths);
    - no backslashes (a normalized-separator dodge);
    - every ``/``-delimited segment is non-empty and is neither ``.`` nor
      ``..`` (no dot segments, no ``//`` runs);
    - its extension is one the endpoint can actually serve
      (``IMAGE_EXTENSIONS``) — a path it could not serve anyway.
    """
    if not path or path.startswith("/") or "\\" in path:
        return False
    segments = path.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        return False
    return _is_image_path(path)


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


# -- Paginated GitHub list endpoints -------------------------------------------

# GitHub clamps ``per_page`` to 100 on every list endpoint, so asking for more
# just wastes the round trip.
_GH_PAGE_SIZE = 100

# "List pull requests files" serves at most 3000 files, i.e. 30 pages of 100.
# The loop stops there itself rather than trusting the sequence to end, so a
# change at GitHub's end can cost a truncated list but never an endless walk.
_GH_MAX_PAGES = 30


class _GitHubError(Exception):
    """A GitHub API list request could not be completed.

    ``status_code`` is the HTTP status that stopped the walk, or ``None`` when
    the request succeeded but the body was not the list the endpoint promises.
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


async def _gh_paginate(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    max_pages: int = _GH_MAX_PAGES,
) -> tuple[list[Any], bool]:
    """Read every page of a GitHub list endpoint.

    Returns ``(items, truncated)``. ``truncated`` is true when the walk ran
    out of pages rather than reaching a short one, so the list may be
    incomplete — it does not establish that more remained. Exactly
    ``max_pages`` full pages with nothing beyond them reports true, because
    settling it would cost a probe request that GitHub cannot answer
    meaningfully at its own ceiling anyway. The flag errs toward "may be
    incomplete", which is the direction that matters for the bug it exists
    to prevent. Raises :class:`_GitHubError` if any page fails.

    Pages are walked by incrementing ``page`` rather than by following the
    ``Link: rel="next"`` header. Both terminate correctly; counting means the
    ``Authorization`` header is only ever sent to a URL this function built,
    and it makes the page cap above a straightforward bound on the walk.

    A short page ends the sequence, so an item count that is an exact
    multiple of ``_GH_PAGE_SIZE`` *below the cap* costs one extra request
    that comes back empty. At the cap itself the walk stops on the page
    count and makes no such request, which is the case above.
    """
    items: list[Any] = []
    base_params = dict(params or {})
    base_params["per_page"] = _GH_PAGE_SIZE

    for page in range(1, max_pages + 1):
        resp = await client.get(url, headers=headers, params={**base_params, "page": page})
        if resp.status_code != 200:
            raise _GitHubError(f"HTTP {resp.status_code}", resp.status_code)

        batch = resp.json()
        if not isinstance(batch, list):
            raise _GitHubError(f"expected a list of items, got {type(batch).__name__}")

        items.extend(batch)
        if len(batch) < _GH_PAGE_SIZE:
            return items, False

    return items, True


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

# ``follow_symlink`` because a Bazel runfiles tree is symlinks into the source
# tree, and Starlette's default treats a symlink pointing outside the mounted
# directory as an escape attempt and 404s it. Under `bazel run //:server` that
# is every asset: the repo page's script, and the favicon the viewer asks for.
# The container is not affected — py_image_layer tars real files — so this is
# the local run that the tests and a reviewer's browser use. Nothing untrusted
# ever lands in this directory; its contents are the two pages and the icon,
# shipped with the app.
app.mount("/static", StaticFiles(directory=static_dir, follow_symlink=True), name="static")


@app.get("/health")
async def health_check():
    """Simple liveness check."""
    return {"status": "ok"}


# -- What the app serves itself -----------------------------------------------
#
# Four routes below take a leading path segment as data: the two page routes
# and the two "/{x}/pr/{n}" routes. Registration order alone is not enough to
# keep them out of the app's own way, because a prefix the app owns can still
# be reached by a path that is not a route: "/api/pulls" is a typo for a real
# endpoint, and answering it with a page hides that. So all four check their
# first segment against this set.
#
# It is deliberately wider than any one route needs. Only "api" and "static"
# can shadow a real two-segment route — "health", "docs", "redoc" and
# "openapi.json" are single-segment, so on "/{owner}/{repo}" those four only
# forbid a repo page for an owner of that name. That is the trade taken
# knowingly: a GitHub user called "docs" is a worse bet than a route this app
# adds later and cannot reach.

_RESERVED_PATH_PREFIXES = frozenset({
    "api",
    "static",
    "health",
    "docs",
    "redoc",
    "openapi.json",
})

# Single-segment paths a browser asks for on its own. Resolving one costs a
# GitHub search request, so they are answered here instead: a page view that
# declares no icon otherwise spends a search — and an hour of cache — on the
# word "favicon.ico".
_RESERVED_IDENTIFIERS = frozenset({
    "favicon.ico",
    "robots.txt",
    "sitemap.xml",
    "apple-touch-icon.png",
    "apple-touch-icon-precomposed.png",
})


# -- Visual review SPA ---------------------------------------------------------

@app.get("/{owner}/{repo}/pr/{number}")
async def visual_review_page(owner: str, repo: str, number: int):
    """Serve the visual review SPA for any owner/repo/PR."""
    if owner in _RESERVED_PATH_PREFIXES:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/{identifier}/pr/{number}")
async def short_url_redirect(identifier: str, number: int):
    """Resolve a short URL and 302-redirect to the canonical path.

    Supports:
    - /{repo_name}/pr/{number} — resolve owner by searching for repo name
    - /{repo_id}/pr/{number} — resolve owner + name by numeric GitHub repo ID
    """
    if identifier in _RESERVED_PATH_PREFIXES:
        return JSONResponse(status_code=404, content={"error": "Not found"})

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

@app.get("/api/extensions")
async def supported_extensions():
    """The image extensions this server understands.

    Exists so a client does not have to keep its own copy of the list. The
    browser extension reads this instead of the copy baked into its bundle, so
    a format added to ``image_extensions.json`` reaches an already-installed
    extension without anyone rebuilding and reloading it.

    ``public`` rather than the ``private`` the proxied images use: this answer
    is derived from a file in the image, not from any repository or token, so
    it is the same for every caller and there is nothing to keep an
    intermediary from holding.

    The hour here is deliberately not the day the browser extension keeps its
    copy for, and the two are independent rather than one being a leftover of
    the other. On the normal path the day governs and this header never comes
    up, because the extension does not re-request inside its own window. The
    hour is what covers the client whose store is gone: where localStorage
    throws — site data blocked, or a quota error — the extension's own cache
    silently never holds, and it asks again on every full page load. This
    header is then the only thing bounding that.

    The CORS header the middleware adds is part of the contract rather than
    incidental. A Manifest V3 content script's ``fetch`` carries the page's
    origin (github.com) and is subject to CORS — ``host_permissions`` cannot
    exempt it, that moved to the service worker in V3 — so this endpoint is
    reachable from the extension only while it answers cross-origin.
    """
    return JSONResponse(
        content={"extensions": list(IMAGE_EXTENSIONS)},
        headers={"Cache-Control": "public, max-age=3600"},
    )


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
    result = {
        "pr_number": number,
        "images": [],
        "base_ref": None,
        "head_ref": None,
        "truncated": False,
    }

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

            # The changed-file list comes from "List pull request files"
            # rather than the compare API, because compare's ``files`` array
            # is capped at 300 entries and offers no page beyond that. Two
            # measurements against apwphotos-appv2 PR 352 (489 changed files,
            # 4 commits), which are separate responses and say different
            # things:
            #
            #   compare/e47246ef...f190bf2d
            #       -> 300 files, and no Link header at all, so nothing
            #          advertises a next page
            #   compare/e47246ef...f190bf2d?per_page=100&page=2
            #       -> 0 files, 0 commits, and a Link header offering only
            #          first and prev
            #
            # Together those say compare paginates its *commits*, not its
            # files: with 4 commits there is one page, so the remaining 189
            # files were unreachable however the request was phrased, and
            # they went missing with nothing said (#12).
            #
            # Both endpoints diff the merge base against the head, so below
            # the cap they agree exactly — measured on three apwphotos-appv2
            # PRs (27, 9 and 4 files): identical path sets both ways. On PR
            # 352 itself compare's 300 paths are a strict subset of the 489,
            # so this adds files rather than exchanging one set for another.
            try:
                files, files_truncated = await _gh_paginate(
                    client,
                    f"https://api.github.com/repos/{github_repo}/pulls/{number}/files",
                    headers,
                )
            except _GitHubError as e:
                return JSONResponse(
                    content={"error": f"Files request failed: {e}", "images": []},
                    headers={"Cache-Control": "no-store"},
                )

            # Set when the walk hit its page cap, i.e. when GitHub may have
            # more files than it served. Left in the payload so a list that
            # may be incomplete says so, rather than repeating this bug one
            # order of magnitude up.
            result["truncated"] = files_truncated

            for f in files:
                filename = f.get("filename", "")
                if _is_image_path(filename):
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

    if not _is_safe_image_path(path):
        logger.warning("pr_image: rejected unsafe path=%r", path)
        return Response(content=b"Invalid image path", status_code=400)

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


# -- Repo page: a repository's open pull requests ------------------------------

# How wide the per-PR fan-out below runs. Each probe is one GitHub request on
# a cold cache, so this bounds both the burst GitHub sees and the number of
# sockets one page view opens, without making a repo with thirty open PRs wait
# for thirty serial round trips.
_PR_PROBE_CONCURRENCY = 6

# The open-PR list is short-lived: a PR opened, merged or retitled should show
# up on the next visit rather than in a quarter of an hour.
_OPEN_PULLS_TTL = 60

# An image count, by contrast, is keyed on the head SHA, so a push changes the
# key rather than ageing the value, and the TTL is not really a staleness
# window: the one thing that can move under a fixed head is the merge base,
# since GitHub diffs a PR against it and a file leaves the diff when the base
# branch gains it. That is a count off by one on a listing page, corrected
# within the quarter hour.
#
# It is not a memory bound either, which is worth saying because the key space
# is unbounded — one entry per (repo, PR, head SHA), so every push to a watched
# PR leaves its predecessor behind. Nothing in this module ever deletes a cache
# key; the TTL governs how long a value is *served*, not how long it is held.
# Eviction is #44, and is the whole cache's problem rather than this key's.
_PR_IMAGE_SUMMARY_TTL = 900

# 1000 open pull requests. Unlike the file list, this is not GitHub's own
# ceiling — it is a bound on how much work one page view can ask for.
_OPEN_PULLS_MAX_PAGES = 10


def _pull_row(pull: dict) -> dict:
    """The fields the repo page shows, from a "List pull requests" entry.

    ``images`` is left None here and filled in by the probe, so a row that was
    never probed and a row whose probe failed are distinguishable from a row
    with no images — the page must never present either as "no images".
    """
    head = pull.get("head") or {}
    base = pull.get("base") or {}
    return {
        "number": pull.get("number"),
        "title": pull.get("title", ""),
        "author": (pull.get("user") or {}).get("login", ""),
        "draft": bool(pull.get("draft", False)),
        "html_url": pull.get("html_url", ""),
        "updated_at": pull.get("updated_at"),
        "head_ref": head.get("ref", ""),
        "head_sha": head.get("sha", ""),
        "base_ref": base.get("ref", ""),
        "labels": [lbl.get("name", "") for lbl in (pull.get("labels") or [])],
        "images": None,
        "images_truncated": False,
        "image_error": None,
    }


async def _pr_image_summary(
    client: httpx.AsyncClient,
    github_repo: str,
    number: int,
    head_sha: str,
    headers: dict[str, str],
) -> dict:
    """How many image files one PR changes.

    Returns ``{"images": int, "truncated": bool}``, or ``{"error": str}`` when
    GitHub could not be asked. An error is deliberately **not** cached and
    carries no count: "we could not tell" has to stay distinct from "none" all
    the way to the page, because the two read identically to whoever is
    deciding which PRs to open.

    **Every failure of the request** returns rather than raises, which is why
    the ``except`` is as wide as it is — and the sentence is that narrow on
    purpose: the count itself is taken outside the ``try``, and
    ``_gh_paginate`` checks that the body is a list without checking that its
    items are dicts, so a malformed entry still raises ``AttributeError`` from
    here. The caller's backstop is what covers that, and this is why it has
    one rather than trusting this docstring. This runs once per open PR inside an
    ``asyncio.gather``, so an exception escaping here does not cost one row —
    it aborts the gather, reaches the endpoint's own handler, and answers with
    an empty list, wiping a page that had already rendered. A read timeout is
    the likely one: ``_GitHubError`` covers a non-200 and a body that is not a
    list, and nothing else. The caller has a place to put a failure, and one
    PR's bad luck is not the page's.
    """
    # The write below is what an empty head SHA is guarded against: it would
    # make a key no push can ever invalidate. The read needs no guard of its
    # own, since a key nothing writes is a key nothing finds.
    cache_key = f"pr_image_summary:{github_repo}:{number}:{head_sha}"
    cached = _cache_get(cache_key, _PR_IMAGE_SUMMARY_TTL)
    if cached is not None:
        return cached

    try:
        files, truncated = await _gh_paginate(
            client,
            f"https://api.github.com/repos/{github_repo}/pulls/{number}/files",
            headers,
        )
    except _GitHubError as e:
        return {"error": f"Files request failed: {e}"}
    except Exception as e:
        # A transport failure is expected and a bug here is not, but both
        # arrive as one "check failed" badge that reads as bad luck. The log
        # is the only place the second is distinguishable from the first.
        #
        # WARNING rather than exception(), because this is the expected branch
        # and it runs once per open PR: a GitHub blip on a repository with
        # twenty-three open pull requests would otherwise be twenty-three
        # ERROR-level tracebacks per cold page load, per visitor. The type is
        # in the message, so a bug is still legible here; the traceback for one
        # is in the caller's backstop, which is the branch nothing expects.
        logger.warning(
            "_pr_image_summary: probe failed repo=%s pr=%s error=%s",
            github_repo, number, f"{type(e).__name__}: {e}",
        )
        return {"error": f"Files request failed: {type(e).__name__}: {e}"}

    summary = {
        "images": sum(1 for f in files if _is_image_path(f.get("filename", ""))),
        "truncated": truncated,
    }
    if head_sha:
        _cache_set(cache_key, summary)
    return summary


@app.get("/api/{owner}/{repo}/pulls")
async def repo_pulls(owner: str, repo: str, probe: bool = Query(True)):
    """List a repository's open PRs, with how many images each one changes.

    Cost, which is the whole design constraint here — the page lists *every*
    open PR, so anything per-PR is multiplied by however many are open:

    - one request for the list itself, plus one more per additional 100 open
      PRs;
    - one request per PR to count its images, only on a cache miss, and only
      when ``probe`` is set — and that is one request per *page* of that PR's
      changed files, so a PR over 100 files costs one per 100 up to
      ``_GH_MAX_PAGES``.

    So N open PRs cost 1 + N requests cold where every PR is under 100 files,
    which is the common case and not the bound: the worst case is 1 + 30N. A
    warm page costs 1. The page asks twice —
    ``probe=0`` first, which is the single cheap request that puts the rows on
    screen, then the full one that fills in the counts — so the list is never
    waiting on the fan-out. Both answers share the list cache, so the second
    request adds no list request of its own.
    """
    github_repo = f"{owner}/{repo}"

    if not GITHUB_TOKEN:
        return JSONResponse(
            content={"error": "No GITHUB_TOKEN configured", "pulls": []},
            headers={"Cache-Control": "no-store"},
        )

    headers = _gh_headers()

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            list_cache_key = f"open_pulls:{github_repo}"
            listing = _cache_get(list_cache_key, _OPEN_PULLS_TTL)
            if listing is None:
                try:
                    pulls, truncated = await _gh_paginate(
                        client,
                        f"https://api.github.com/repos/{github_repo}/pulls",
                        headers,
                        params={"state": "open", "sort": "updated", "direction": "desc"},
                        max_pages=_OPEN_PULLS_MAX_PAGES,
                    )
                except _GitHubError as e:
                    return JSONResponse(
                        content={"error": f"Pull request list failed: {e}", "pulls": []},
                        headers={"Cache-Control": "no-store"},
                    )
                listing = {
                    "pulls": [_pull_row(p) for p in pulls],
                    "truncated": truncated,
                }
                _cache_set(list_cache_key, listing)

            # Copied out of the cache before anything is filled in. The cached
            # listing is keyed on the repository alone, so a probe result
            # written into it would outlive the head SHA it was measured
            # against; the summaries have their own per-SHA cache for that.
            rows = [dict(row) for row in listing["pulls"]]

            if probe and rows:
                limit = asyncio.Semaphore(_PR_PROBE_CONCURRENCY)

                async def fill(row: dict) -> None:
                    # A second guard over _pr_image_summary's own promise never
                    # to raise, because the cost of that promise being broken
                    # is out of all proportion to one row: a gather re-raises
                    # the first exception it sees, which reaches the handler
                    # below and answers with no pulls at all, so one PR's read
                    # timeout would blank a page that had already rendered the
                    # other twenty-nine. Here it is one row reading "check
                    # failed", which is what it is.
                    try:
                        async with limit:
                            summary = await _pr_image_summary(
                                client, github_repo, row["number"], row["head_sha"], headers,
                            )
                    except Exception as e:
                        # Reaching here means the promise above was broken, so
                        # this one is logged whatever it turns out to be.
                        logger.exception(
                            "repo_pulls: probe raised repo=%s pr=%s",
                            github_repo, row["number"],
                        )
                        summary = {"error": f"Probe failed: {type(e).__name__}: {e}"}
                    if "error" in summary:
                        row["image_error"] = summary["error"]
                    else:
                        row["images"] = summary["images"]
                        row["images_truncated"] = summary["truncated"]

                await asyncio.gather(*(fill(row) for row in rows))

    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "pulls": []},
            headers={"Cache-Control": "no-store"},
        )

    return JSONResponse(
        content={
            "repo": github_repo,
            "pulls": rows,
            "truncated": listing["truncated"],
            "probed": probe,
        },
        headers={"Cache-Control": "no-store"},
    )


# -- Page routes for a repository ----------------------------------------------
#
# These two are catch-alls: "/{owner}/{repo}" matches any two segments and
# "/{identifier}" any one. They are registered here, last, so that every route
# above wins on its own — a two-segment route added to this file later is
# reached rather than silently swallowed, which for a page route means a 200
# with the wrong body, the worst shape that mistake can take.
#
# The two older catch-alls, visual_review_page and short_url_redirect, are
# *not* last: they predate this and sit above every /api/ route. Moving them
# is not free — they are three and four segments deep, so the routes they can
# shadow are narrower — and they carry the same _RESERVED_PATH_PREFIXES guard
# instead, which is what stops "/api/x/pr/5" being served the viewer. So the
# ordering assertion below covers these two only, and says so; what covers all
# four is the guard, and the census test that finds every route taking a
# leading segment as data. For those two the guard is the whole of the
# protection rather than a second line of it.


@app.get("/{owner}/{repo}")
async def repo_page(owner: str, repo: str):
    """Serve the repo page listing a repository's open pull requests."""
    if owner in _RESERVED_PATH_PREFIXES:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return FileResponse(os.path.join(static_dir, "repo.html"))


@app.get("/{identifier}")
async def short_repo_redirect(identifier: str):
    """Resolve a short repo identifier and 302-redirect to its repo page.

    The repo-page counterpart of :func:`short_url_redirect`, so that a short
    link keeps working with the PR number taken off the end: ``/im495z/pr/50``
    names a PR and ``/im495z`` names the repository it belongs to.
    """
    if identifier in _RESERVED_IDENTIFIERS or identifier in _RESERVED_PATH_PREFIXES:
        return JSONResponse(status_code=404, content={"error": "Not found"})

    matches = await _resolve_repo(identifier)

    if len(matches) == 1:
        owner, repo = matches[0]
        return RedirectResponse(url=f"/{owner}/{repo}", status_code=302)

    if len(matches) > 1:
        return JSONResponse(
            status_code=300,
            content={
                "error": "Ambiguous repository name",
                "identifier": identifier,
                "matches": [
                    {"owner": owner, "repo": repo, "url": f"/{owner}/{repo}"}
                    for owner, repo in matches
                ],
            },
        )

    return JSONResponse(
        status_code=404,
        content={"error": f"Repository not found: {identifier}"},
    )


# -- Root redirect -------------------------------------------------------------

@app.get("/")
async def root():
    """Show a simple landing page."""
    return JSONResponse(content={
        "app": "Visual Review",
        "usage": "Navigate to /{owner}/{repo}/pr/{number} to review a PR's visual changes.",
        "short_urls": "Also supports /{repo_name}/pr/{number}, /{repo_id}/pr/{number}, and /{base36_id}/pr/{number}.",
        "repo_page": "Navigate to /{owner}/{repo} to list a repository's open pull requests and see which of them change images.",
    })
