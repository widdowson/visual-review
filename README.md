# Visual Review

A standalone tool for reviewing visual changes (PNG screenshots) in GitHub pull requests. Compare baseline images side-by-side, with crossfade, swipe, or pixel diff overlays.

## Features

- **Multi-repo support** — one deployment serves any GitHub repository via `/{owner}/{repo}/pr/{number}`
- **Repo page** — `/{owner}/{repo}` lists every open pull request and says which of them change images, so you can tell what is worth opening without checking each one on GitHub
- **4 comparison modes** — side-by-side, crossfade, swipe slider, and pixel diff overlay
- **Pixel loupe** — hold Shift to magnify and inspect individual pixels across base, current, and diff views
- **Diff gutter** — minimap showing which rows have changes, with scroll indicators
- **Per-file comments** — read and post GitHub PR review comments inline
- **Keyboard shortcuts** — vim-style navigation (j/k for files, n/p for diff regions, 1-4 for modes)
- **Dark mode** — automatic or manual light/dark theme toggle
- **Deep linking** — link directly to a specific file via URL hash
- **Unambiguous file list** — where a PR repeats a filename across directories, each sidebar row shows the shortest directory context that tells it apart

## Quick Start

### Docker (recommended)

```bash
# Clone and start
git clone https://github.com/your-org/visual-review.git
cd visual-review
printf 'GITHUB_TOKEN=ghp_your_token_here\nVR_AUTH_MODE=disabled\n' > .env
docker compose up

# Open in browser
open http://localhost:8080/widdowson/apwphotos-appv2/pr/50
```

### Local Development

```bash
pip install -r requirements.txt
export GITHUB_TOKEN=ghp_your_token_here
# Nothing issues an Access token on localhost, so run with the gate off.
export VR_AUTH_MODE=disabled
uvicorn app:app --reload --port 8080
```

### Running the tests

Everything runs under Bazel, including the Chrome extension's JS tests and the
tests that exercise logic inlined in `static/index.html`:

```bash
bazel test //...
```

The JS tests also run standalone, which is quicker while iterating on the SPA:

```bash
node tests/test_prefetch_policy.js
node tests/test_image_urls.js
node tests/test_hash_target.js
```

`pytest tests/ -v` still works for the Python tests alone, but it misses every
`js_test`, so it is not enough before pushing.

`//:test_spa_prefetch` drives the real SPA in a browser, against a fake backend
(`tests/fixture_server.py`) rather than GitHub. Chromium is hermetic — Bazel
downloads it, pinned in `MODULE.bazel` to the build matching the `playwright`
pin in `requirements_lock.txt`. Those two must move together, and the test
asserts it rather than trusting this paragraph — the version in the manifest's
filename, and each entry's `revision` and `browserVersion`, against the
`browsers.json` the installed wheel ships. `revision` is what selects the
download, since the URL is `builds/<name>/<revision>/…`; `browserVersion` says
which build that revision *is*, and selects nothing, so it is checked to catch
a manifest edited by hand rather than re-derived. Bump `playwright` and the
trimmed manifest is to be re-derived from that file, not edited. Nothing needs
to be installed and `playwright install` must not be run.

**Which Linux build it fetches is a build flag, not autodetection.**
`@rules_playwright//:linux_distro` defaults to `ubuntu24.04`, so on Debian 12
or Ubuntu 22.04 Bazel will fetch a shell built for the wrong distro and the
failure surfaces as an opaque browser launch error. Pass the right one:

```bash
bazel test //... --@rules_playwright//:linux_distro=debian12
```

Accepted values are `debian11`, `debian12`, `ubuntu20.04`, `ubuntu22.04` and
`ubuntu24.04`. CI pins `runs-on: ubuntu-24.04` so the runner and the default
cannot drift apart.

To poke at the fixture by hand, serve it and open the URL it prints:

```bash
python3 tests/fixture_server.py
```

### Google Cloud Run

The app is stateless and scales to zero, making Cloud Run an ideal deployment target — you only pay when someone is actively reviewing a PR.

```bash
# Build and deploy
gcloud run deploy visual-review \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GITHUB_TOKEN=ghp_your_token_here,\
CF_ACCESS_TEAM_DOMAIN=yourteam.cloudflareaccess.com,CF_ACCESS_AUD=<aud-tag>

# The deploy command outputs a service URL like:
# https://visual-review-xxxxx-uc.a.run.app
```

`--allow-unauthenticated` here means "no *Google* IAM check"; the app's own
Cloudflare Access gate is what authenticates callers. See **Authentication**
below — without `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` the service refuses
to start rather than coming up open.

**Custom domain (e.g. `vr.apw.photos`):**

1. Map the domain in Cloud Run:
   ```bash
   gcloud run domain-mappings create \
     --service visual-review \
     --domain vr.apw.photos \
     --region us-central1
   ```
2. In Cloudflare DNS, add a CNAME record:
   - Name: `vr`
   - Target: `ghs.googlehosted.com` (or the target from the domain mapping output)
   - Proxy: enabled (orange cloud)

### Other Cloud Platforms

Also works on Fly.io, Railway, or any platform that runs Docker containers. The Dockerfile listens on `$PORT` (default 8080) which is the standard convention for Cloud Run, Fly.io, and others.

The only required secret is `GITHUB_TOKEN` — a GitHub personal access token with `repo` scope (for private repos) or `public_repo` scope (for public repos only).

Note what a `repo`-scoped token means for a deployment: the server will read file
bytes from any repository that token can reach, for whoever can reach the server.
That is why the Cloudflare Access gate above is not optional on a public
deployment holding one.

## URL Scheme

```
/{owner}/{repo}/pr/{number}          → Visual review SPA
/{owner}/{repo}                      → Repo page: open PRs, and which change images
/{identifier}                        → Short form of the repo page (302)
/api/{owner}/{repo}/...              → API endpoints
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/{owner}/{repo}/pr/{number}/images` | List changed PNG files in the PR |
| GET | `/api/{owner}/{repo}/pr/{number}/image?path=...&ref=...` | Proxy image content from a git ref |
| GET | `/api/{owner}/{repo}/pr/{number}/comments?path=...` | Get review comments for a file |
| POST | `/api/{owner}/{repo}/pr/{number}/comments` | Post a review comment on a file |
| GET | `/api/{owner}/{repo}/pr/{number}/comment-counts` | Get comment counts by file |
| GET | `/api/{owner}/{repo}/pr/{number}/checks` | Combined CI status for the PR's head commit |
| GET | `/api/{owner}/{repo}/pulls?probe=1` | List open PRs with each one's image count. `probe=0` returns the list alone, with no per-PR count, in a single request |
| GET | `/api/extensions` | Image extensions this server understands, for clients that would otherwise hardcode them |
| GET | `/api/me` | The signed-in address, or `authenticated: false` |
| GET | `/health` | Liveness check (the one path the auth gate exempts) |

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GITHUB_TOKEN` | Yes | — | GitHub personal access token |
| `PORT` | No | `8080` | Port to listen on (set automatically by Cloud Run) |
| `VR_AUTH_MODE` | No | `cloudflare_access` | `cloudflare_access` or `disabled` |
| `CF_ACCESS_TEAM_DOMAIN` | When enabled | — | `yourteam.cloudflareaccess.com` (the bare team name also works) |
| `CF_ACCESS_AUD` | When enabled | — | The Access application's AUD tag |
| `VR_ALLOWED_EMAILS` | No | — | Optional comma-separated allowlist, checked after the token verifies |

## Authentication

The server verifies the Cloudflare Access JWT on every request. Anything that
did not come through Access is refused with a 403, whatever hostname or address
it arrived on. `/health` is the only exempt path, because Cloud Run's liveness
probe does not come through Cloudflare either.

**Why the app does this rather than leaving it to Access.** Access runs at
Cloudflare's edge, and the edge is not the only route to the origin. A Cloud Run
service answers any request carrying the mapped `Host` header, and the address
it answers on — `ghs.googlehosted.com` — is the documented CNAME target for
every Cloud Run domain mapping, so it is not a secret. Turning on the orange
cloud puts a login page in front of the *name* and leaves the origin reachable.
Verifying the signature here is what actually closes it.

**It fails closed.** `VR_AUTH_MODE` defaults to `cloudflare_access`, and with
the gate on but unconfigured the app raises at startup instead of serving. To
run without authentication — local development, and the test suite — say so:

```bash
VR_AUTH_MODE=disabled uvicorn app:app --reload --port 8080
```

**Finding the two values.** In the Cloudflare Zero Trust dashboard the team
domain is under Settings → Custom Pages (it is also the `<team>.cloudflareaccess.com`
shown on any Access login page), and the AUD tag is on the Access application's
own Overview tab. The AUD is per-application: it is what stops a token minted
for some *other* Access application on the same team from opening this one.

**`VR_ALLOWED_EMAILS` is a second gate, not the first.** The Access policy is
what decides who can sign in at all. Setting this as well means that if the
policy is ever widened by accident, the app still serves only the addresses it
was configured with.

### What the app learns about the person

Only their email address. An Access token carries `aud`, `email`, `exp`, `iat`,
`iss`, `sub`, `type`, `identity_nonce` and `country` — there is no display name
in it, and Access exposes no avatar at all. `/api/me` returns that address, and
the SPA draws a chip in the top right with initials derived from it rather than
fetching a picture from a third party that would then learn who reviews what.

A display name *is* available from `<team>.cloudflareaccess.com/cdn-cgi/access/get-identity`
when the team authenticates against a real identity provider such as Google;
with one-time-PIN login there is no name to have. No avatar either way.

## How It Works

1. The SPA parses `owner`, `repo`, and PR number from the URL path
2. The API fetches PR metadata and changed files from GitHub's API
3. Images are proxied through the server to avoid CORS issues with canvas-based pixel diffing
4. All comparison modes (side-by-side, crossfade, swipe, diff) work client-side using HTML5 Canvas
5. Comments are read/written via GitHub's PR review comments API

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `n` | Next diff region (within file) |
| `p` | Previous diff region (within file) |
| `N` / `j` / `↓` | Next file |
| `P` / `k` / `↑` | Previous file |
| `1` | Side by side mode |
| `2` | Crossfade mode |
| `3` | Swipe mode |
| `4` | Diff overlay mode |
| `Shift` | Hold to magnify (pixel loupe) |
| `?` | Toggle shortcut help |

## License

MIT
