"""A fake Visual Review backend, for driving the SPA in a real browser.

The SPA under test is `static/index.html`, served verbatim. Everything behind
it is faked here rather than reaching GitHub, for three reasons: the tests stay
hermetic, an image can be made deliberately slow so that "still in flight" is
an observable state, and — the reason this exists at all — **the server's own
request log is the measuring instrument**.

That last point is what makes the end-to-end assertions possible. The property
the prefetch work exists for is "the next file renders from memory", and the
only honest way to check it is that no request reaches the server when the user
moves onto a file that was prefetched. Likewise "a fast scroll does not leave a
pile of transfers racing" is a statement about requests, not about pixels. The
browser cannot be asked either question; this server can answer both.

Aborts are recorded too. `img.src = ''` makes Chromium close the connection, so
a write into a half-delivered response raises, and the record is closed with
``aborted: True``. That is a real signal from the browser, not an inference.

**A test can also hold a response open** (`FixtureState.hold`), which is how
an interleaving is driven rather than hoped for. A sleep orders two responses
only on a machine fast enough to honour it; a hold orders them by construction,
because nothing is sent until the test says so. A held request whose client
goes away is closed as aborted while it is still held, so "the page cancelled
this" is observable without first releasing it.

What the page is shown is the test's to choose: `files` replaces the generated
file list, `comments` gives each path its own comment thread, and `images`
replaces what one side of one file serves — other bytes, or an HTTP error
status in place of the PNG. All three default to what the prefetch tests were
written against, so a test that does not care gets a payload that works.

`POST .../comments` is served as well. It appends the posted comment to that
path's thread, so the reload the page makes after posting reads it back, and it
can be held like the two GET routes (`hold("comment_post", path)`). Nothing is
appended until the hold is released, so a GET that overtakes a held POST sees
the thread as it was.
"""

import json
import os
import select
import socket
import struct
import sys
import threading
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import _image_cache_control  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE_REF = "a" * 40
HEAD_REF = "b" * 40


def png_bytes(seed: int, size: int = 64, alpha: int | None = None,
              height: int | None = None,
              rgb: tuple[int, int, int] | None = None) -> bytes:
    """A solid-colour PNG, its colour derived from `seed`.

    Written by hand rather than with Pillow: this repo has no image library in
    its test deps and does not need one for a coloured square. Distinct colours
    matter because a test asserting "file 3 is on screen" reads a pixel.

    `alpha` makes it an RGBA PNG at that opacity, with the colour unchanged, so
    two images can be built that differ in alpha alone — which is what the
    diff gutter has to be shown not to compare. `height` makes it `size` wide
    and that tall, for a test that needs the image taller than the viewport.
    `rgb` names the colour outright in place of the seed's, for a pair that has
    to differ by an exact amount.
    """
    rows = size if height is None else height
    r, g, b = rgb if rgb is not None else (
        (seed * 53) % 256, (seed * 97) % 256, (seed * 149) % 256)
    pixel = bytes([r, g, b]) if alpha is None else bytes([r, g, b, alpha])
    raw = b"".join(b"\x00" + pixel * size for _ in range(rows))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, rows, 8,
                                         2 if alpha is None else 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


# How long a hold waits for a release before giving up on the test. Long enough
# that no honest test reaches it, short enough that a test which forgot to
# release does not leave handler threads parked for the rest of the run. An
# expiry closes the record as aborted, never as delivered, and is recorded in
# FixtureState.expired_holds, which spa_backend's teardown fails the test on:
# raising in the handler thread would reach only stderr.
HOLD_TIMEOUT = 60.0


class Hold:
    """One response the fixture keeps open until the test calls `release()`.

    Matches on route kind and path, and on ref when one is given, so a test can
    hold one side of a comparison pair and let the other through. It holds
    every matching request until released, not only the first: a page that
    retried would otherwise get the answer the test was withholding.
    """

    def __init__(self, kind: str, path: str, ref: str | None):
        self.kind, self.path, self.ref = kind, path, ref
        self.reached = threading.Event()   # a matching request is being held
        self._released = threading.Event()

    def matches(self, kind: str, path: str, ref: str | None) -> bool:
        return (kind == self.kind and path == self.path
                and (self.ref is None or ref == self.ref))

    def release(self) -> None:
        self._released.set()

    @property
    def released(self) -> bool:
        return self._released.is_set()

    def wait(self, timeout: float) -> bool:
        return self._released.wait(timeout)


class FixtureState:
    """Everything a test can vary or read, shared by the handler threads."""

    def __init__(self, file_count: int = 3, renamed_index: int | None = None,
                 signed_in_email: str | None = None,
                 files: list[dict] | None = None,
                 comments: dict[str, list[dict]] | None = None,
                 images: dict[tuple[str, str], bytes | int] | None = None):
        # `files` replaces the generated list outright, rather than being
        # merged with it, so that what a test passes is exactly what the page
        # is shown. The generated list is kept for the tests written against
        # it, and so that a test which does not care still gets a PR.
        if files is not None and renamed_index is not None:
            raise ValueError("renamed_index applies to the generated list; "
                             "give the entry a previous_filename instead")
        self.file_count = len(files) if files is not None else file_count
        self.explicit_files = [dict(f) for f in files] if files is not None else None
        self.renamed_index = renamed_index
        self.signed_in_email = signed_in_email
        self.comments = {k: list(v) for k, v in (comments or {}).items()}
        # Keyed by (path, side), side being "base" or "head" rather than a ref,
        # so a test need not know the refs. Bytes are served in place of the
        # generated PNG; an int is the HTTP status to fail with instead, which
        # is how a missing side is modelled (#40).
        self.images = dict(images or {})
        for key, value in self.images.items():
            if (not isinstance(key, tuple) or len(key) != 2
                    or key[1] not in ("base", "head")):
                raise ValueError(f"images keys are (path, 'base'|'head'), not {key!r}")
            if isinstance(value, bool) or not isinstance(value, (bytes, int)):
                raise ValueError(f"an image override is bytes or a status, not "
                                 f"{type(value).__name__}")
            if isinstance(value, int) and not 400 <= value < 600:
                raise ValueError(f"an image override that is a status must be an "
                                 f"error status, not {value}")
        self.holds: list[Hold] = []
        self.expired_holds: list[str] = []
        self.image_delay = 0.30       # seconds spread across the response body
        # Asked of app.py rather than copied from it. The SPA never displays
        # the Image objects it decodes — every comparison mode builds fresh
        # <img> elements from `state.baseImg.src` — so the pixels on screen
        # come out of the HTTP cache, and this header is load-bearing for the
        # feature rather than a nicety. Serve these `no-store` and "renders
        # from memory" stops working altogether, which is how the fixture
        # found out. A hard-coded copy would go on asserting that about a
        # header production had stopped sending.
        self.cache_control = _image_cache_control(HEAD_REF)
        self.log: list[dict] = []
        self.lock = threading.Lock()
        self.t0 = time.monotonic()

    def files(self) -> list[dict]:
        if self.explicit_files is not None:
            return [dict(f) for f in self.explicit_files]
        out = []
        for i in range(self.file_count):
            entry = {"path": f"shots/file_{i:02d}.png", "status": "modified",
                     "additions": 1, "deletions": 1}
            if i == self.renamed_index:
                entry["status"] = "renamed"
                entry["previous_filename"] = f"shots/old_name_{i:02d}.png"
            out.append(entry)
        return out

    def open_record(self, kind: str, **fields) -> dict:
        rec = {"kind": kind, "start": time.monotonic() - self.t0,
               "end": None, "aborted": False, "held": False,
               "timed_out": False, **fields}
        with self.lock:
            self.log.append(rec)
        return rec

    def close_record(self, rec: dict, aborted: bool = False) -> None:
        with self.lock:
            rec["end"] = time.monotonic() - self.t0
            rec["aborted"] = aborted

    def hold(self, kind: str, path: str, ref: str | None = None) -> Hold:
        """Keep matching responses open until the returned hold is released.

        `kind` is the route: "image", "comments" (the GET) or "comment_post".
        Takes effect for requests that arrive after the call, so set it up
        before driving the page.
        """
        if kind not in ("image", "comments", "comment_post"):
            raise ValueError(f"nothing honours a hold on {kind!r}")
        if kind != "image" and ref is not None:
            # The comments endpoints have no ref, so this hold could never match.
            raise ValueError(f"a {kind} hold cannot name a ref")
        h = Hold(kind, path, ref)
        with self.lock:
            self.holds.append(h)
        return h

    def release_all(self) -> None:
        with self.lock:
            holds = list(self.holds)
        for h in holds:
            h.release()

    def _holds_for(self, kind: str, path: str, ref: str | None) -> list[Hold]:
        with self.lock:
            return [h for h in self.holds
                    if not h.released and h.matches(kind, path, ref)]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: FixtureState = None  # set on the subclass created in serve()
    index_html: bytes = b""

    def log_message(self, *args):  # keep pytest output readable
        pass

    # ── helpers ─────────────────────────────────────────────────────────────

    def _send(self, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj) -> None:
        self._send(json.dumps(obj).encode(), "application/json",
                   {"Cache-Control": "no-store"})

    def _client_gone(self) -> bool:
        """Has the browser closed this connection?

        A request is read in full before it is answered, and Chromium does not
        pipeline, so the socket has nothing more to say until the response
        goes out. Readable with nothing to read is therefore end-of-file:
        the page cancelled the request.
        """
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def _await_holds(self, rec: dict, kind: str, path: str,
                     ref: str | None = None) -> bool:
        """Block while a hold matches. False if the client left meanwhile.

        Polled rather than parked on the event, because the other way out of
        a hold is the browser giving up on the request, and nothing signals
        that except the socket.
        """
        holds = self.state._holds_for(kind, path, ref)
        if not holds:
            return True
        with self.state.lock:
            # Under the lock: /__probe/log serialises these dicts on another
            # thread, and a key added mid-dump raises there.
            rec["held"] = True
        deadline = time.monotonic() + HOLD_TIMEOUT
        for h in holds:
            h.reached.set()
        for h in holds:
            while not h.wait(0.02):
                if self._client_gone():
                    return False
                if time.monotonic() > deadline:
                    with self.state.lock:
                        rec["timed_out"] = True
                        self.state.expired_holds.append(
                            f"a hold on {kind} {path} was never released")
                    return False
        return True

    # ── routes ──────────────────────────────────────────────────────────────

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/__probe/reset":
            with self.state.lock:
                self.state.log.clear()
            return self._json({"ok": True})
        if path.endswith("/comments"):
            return self._post_comment()
        self.send_error(404)

    def _post_comment(self) -> None:
        # Read in full before anything else, so the socket has nothing left to
        # say and _client_gone can read end-of-file as the page walking away.
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        file_path, body = payload.get("path", ""), payload.get("body", "")
        rec = self.state.open_record("comment_post", path=file_path, body=body)
        if not self._await_holds(rec, "comment_post", file_path):
            return self.state.close_record(rec, aborted=True)
        # Appended only once released, so a GET answered while this was held
        # reads the thread as it stood before the post.
        with self.state.lock:
            self.state.comments.setdefault(file_path, []).append({
                "user": "you", "body": body,
                "created_at": "2026-10-01T12:00:00Z",
                "html_url": "https://example.invalid/posted"})
        try:
            self._json({"ok": True})
        except OSError:
            return self.state.close_record(rec, aborted=True)
        self.state.close_record(rec)

    def do_GET(self):
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)

        if path == "/api/me":
            # The signed-in chip's only input. None models VR_AUTH_MODE=disabled,
            # where the app answers authenticated=false and the chip stays
            # hidden.
            if self.state.signed_in_email is None:
                return self._json({"authenticated": False})
            return self._json({"authenticated": True,
                               "email": self.state.signed_in_email})
        if path == "/__probe/log":
            with self.state.lock:
                return self._json(list(self.state.log))
        if path.endswith("/images"):
            rec = self.state.open_record("images")
            self.state.close_record(rec)
            return self._json({
                "images": self.state.files(),
                "base_ref": BASE_REF, "head_ref": HEAD_REF,
                "pr_title": "Fixture PR", "pr_url": "https://example.invalid/pr/1",
            })
        if path.endswith("/image"):
            return self._image(query)
        if path.endswith("/comments"):
            return self._comments(query)
        if path.endswith("/comment-counts"):
            # Derived from the threads rather than configured beside them, so
            # the sidebar's badges and the pane cannot disagree about a file.
            with self.state.lock:
                counts = {p: len(t) for p, t in self.state.comments.items() if t}
            return self._json({"counts": counts})
        if path.endswith("/checks"):
            return self._json({"checks": [], "state": "success"})
        if "/pr/" in path:
            return self._send(self.index_html, "text/html; charset=utf-8")
        self.send_error(404)

    def _comments(self, query: dict) -> None:
        file_path = (query.get("path") or [""])[0]
        rec = self.state.open_record("comments", path=file_path)
        if not self._await_holds(rec, "comments", file_path):
            return self.state.close_record(rec, aborted=True)
        with self.state.lock:
            thread = list(self.state.comments.get(file_path, []))
        try:
            self._json({"comments": thread, "truncated": False})
        except OSError:
            return self.state.close_record(rec, aborted=True)
        self.state.close_record(rec)

    def _image(self, query: dict) -> None:
        img_path = (query.get("path") or [""])[0]
        ref = (query.get("ref") or [""])[0]
        rec = self.state.open_record("image", path=img_path, ref=ref)

        # A distinct colour per (path, side) so a test can read a pixel and say
        # which file is on screen, and which side of the comparison it is.
        # crc32, not hash(): str hashing is salted per process, so hash()
        # would give a different colour on every run and a test that reads
        # a pixel could not state what it expects.
        seed = (zlib.crc32(img_path.encode()) % 200) + (7 if ref == HEAD_REF else 0)
        side = "head" if ref == HEAD_REF else "base"
        override = self.state.images.get((img_path, side))
        body = override if isinstance(override, bytes) else png_bytes(seed)

        aborted = False
        try:
            # Inside the try, so that a hold the browser walks away from is
            # closed as an abort by the same finally as one that lands
            # mid-body, and a forgotten hold still closes its record.
            if not self._await_holds(rec, "image", img_path, ref):
                aborted = True
                return
            if isinstance(override, int):
                with self.state.lock:
                    rec["status"] = override
                self.send_response(override)
                # no-store, because a 404 is heuristically cacheable and a
                # test that later serves the same side successfully should
                # not be answered from a cached failure.
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", self.state.cache_control)
            self.end_headers()

            # Delivered in slices so that an abort lands mid-body and raises,
            # rather than the whole response going out before the browser has
            # a chance to change its mind.
            slices = 6
            step = max(1, len(body) // slices)
            offsets = list(range(0, len(body), step))
            pause = self.state.image_delay / max(1, len(offsets) - 1)
            for n, off in enumerate(offsets):
                if n:  # pause between slices, never after the last one, so a
                       # record closes when the bytes are out rather than a
                       # slice-time later
                    time.sleep(pause)
                self.wfile.write(body[off:off + step])
                self.wfile.flush()
        except OSError:
            # Any failure to write is the client having gone: a bare OSError or
            # a ConnectionAbortedError counts as much as a BrokenPipeError. The
            # close must also happen on an error nobody anticipated, because a
            # record left open reads as a transfer racing forever and surfaces
            # as an unrelated wait_until timeout somewhere else.
            aborted = True
        finally:
            # Two residues of closing in a finally, both preferred to the hang:
            # an exception that is not an OSError closes the record as a
            # *successful* transfer, and a genuine server-side OSError closes it
            # as a browser abort, which test_abandoned_transfers_are_cancelled
            # would accept. Both mean the fixture itself broke, which is a
            # louder failure than either reading.
            self.state.close_record(rec, aborted=aborted)


def serve(index_html_path: str, **state_kwargs,
          ) -> tuple[ThreadingHTTPServer, FixtureState]:
    """Start the fixture on an ephemeral port. Caller shuts it down.

    Keyword arguments are FixtureState's. Tests normally reach this through
    the `spa_backend` fixture in tests/conftest.py, which also shuts it down.
    """
    state = FixtureState(**state_kwargs)
    with open(index_html_path, "rb") as fh:
        html = fh.read()

    handler = type("BoundHandler", (Handler,),
                   {"state": state, "index_html": html})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, state


if __name__ == "__main__":  # manual poking: python tests/fixture_server.py
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    srv, st = serve(os.path.join(here, "static", "index.html"), file_count=17)
    print(f"http://127.0.0.1:{srv.server_address[1]}/owner/repo/pr/1")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()
