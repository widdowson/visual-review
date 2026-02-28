# Visual Review

A standalone tool for reviewing visual changes (PNG screenshots) in GitHub pull requests. Compare baseline images side-by-side, with crossfade, swipe, or pixel diff overlays.

## Features

- **Multi-repo support** — one deployment serves any GitHub repository via `/{owner}/{repo}/pr/{number}`
- **4 comparison modes** — side-by-side, crossfade, swipe slider, and pixel diff overlay
- **Pixel loupe** — hold Shift to magnify and inspect individual pixels across base, current, and diff views
- **Diff gutter** — minimap showing which rows have changes, with scroll indicators
- **Per-file comments** — read and post GitHub PR review comments inline
- **Keyboard shortcuts** — vim-style navigation (j/k for files, n/p for diff regions, 1-4 for modes)
- **Dark mode** — automatic or manual light/dark theme toggle
- **Deep linking** — link directly to a specific file via URL hash

## Quick Start

### Docker (recommended)

```bash
# Clone and start
git clone https://github.com/your-org/visual-review.git
cd visual-review
echo "GITHUB_TOKEN=ghp_your_token_here" > .env
docker compose up

# Open in browser
open http://localhost:8000/widdowson/apwphotos-appv2/pr/50
```

### Local Development

```bash
pip install -r requirements.txt
export GITHUB_TOKEN=ghp_your_token_here
uvicorn app:app --reload

# Run tests
pip install pytest pytest-asyncio
pytest tests/ -v
```

### Cloud Deployment

Works on Fly.io, Railway, Google Cloud Run, or any platform that runs Docker containers.

The only required secret is `GITHUB_TOKEN` — a GitHub personal access token with `repo` scope (for private repos) or `public_repo` scope (for public repos only).

## URL Scheme

```
/{owner}/{repo}/pr/{number}          → Visual review SPA
/api/{owner}/{repo}/pr/{number}/...  → API endpoints
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/{owner}/{repo}/pr/{number}/images` | List changed PNG files in the PR |
| GET | `/api/{owner}/{repo}/pr/{number}/image?path=...&ref=...` | Proxy image content from a git ref |
| GET | `/api/{owner}/{repo}/pr/{number}/comments?path=...` | Get review comments for a file |
| POST | `/api/{owner}/{repo}/pr/{number}/comments` | Post a review comment on a file |
| GET | `/api/{owner}/{repo}/pr/{number}/comment-counts` | Get comment counts by file |
| GET | `/health` | Liveness check |

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GITHUB_TOKEN` | Yes | — | GitHub personal access token |

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
