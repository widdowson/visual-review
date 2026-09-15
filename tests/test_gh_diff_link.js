// Exercises ghDiffUrl, which lives in static/index.html: the link from a file
// in the viewer to that same file's diff on the GitHub PR (issue #15).
//
// The anchor format is GitHub's, not ours, and it is the entire feature — get
// the hash wrong and every link lands at the top of the Files tab, which looks
// like it worked. So the first assertion below is GitHub's own published
// worked example rather than a value read off our implementation, and the rest
// check the digest against node's sha256 instead of against ghDiffUrl itself.

const assert = require('assert');
const crypto = require('crypto');
const { extract, bodyOf } = require('./spa_source');

const ghDiffUrl = extract('gh-diff-link', 'ghDiffUrl');

const OWNER = 'widdowson';
const REPO = 'apwphotos-appv2';
const PR = 402;
const anchorOf = url => url.slice(url.indexOf('#') + 1);
const sha256 = s => crypto.createHash('sha256').update(s, 'utf8').digest('hex');

async function main() {

// ── GitHub's format ─────────────────────────────────────────────────────────

// GitHub documents `src/index.js` as anchoring at this hash. Both halves of
// the pair are theirs, so this fails if GitHub ever changes the scheme — which
// is the point: nothing else in this repo would notice.
assert.strictEqual(
  await ghDiffUrl('pallets', 'flask', 7, 'src/index.js'),
  'https://github.com/pallets/flask/pull/7/files' +
    '#diff-bfe9874d239014961b1ae4e89875a6155667db834a410aaaa2ebe3cf89820556',
  'the anchor must be diff- + sha256 hex of the file path, on the /files tab');

// ── The digest ──────────────────────────────────────────────────────────────

// Checked against node's own sha256 over the path's exact bytes, which pins
// the two ways this silently goes wrong: hashing something other than the
// path, and hashing the path plus a trailing newline.
for (const path of [
  'django/apps/proofing/tests/visual/cuj_proofing_06_quota_chips.bmp',
  'a.png',
  'dir/sub/file name with spaces.png',
  'accénts/café.bmp',
]) {
  const url = await ghDiffUrl(OWNER, REPO, PR, path);
  assert.strictEqual(anchorOf(url), 'diff-' + sha256(path),
    'the anchor for ' + path + ' must be sha256 of exactly that path');
  assert.notStrictEqual(anchorOf(url), 'diff-' + sha256(path + '\n'),
    'the path must be hashed without a trailing newline');
}

// Lower-case hex, zero-padded to two characters a byte. The first path above
// has three bytes below 0x10, so a missing padStart shortens this fragment.
const padded = anchorOf(await ghDiffUrl(OWNER, REPO, PR,
  'django/apps/proofing/tests/visual/cuj_proofing_06_quota_chips.bmp'));
assert.ok(/^diff-[0-9a-f]{64}$/.test(padded),
  'the fragment must be 64 lower-case hex characters, got ' + padded);

// The hash is of the path alone: the same file anchors identically whichever
// PR it is viewed under, and two files never share an anchor.
assert.strictEqual(
  anchorOf(await ghDiffUrl(OWNER, REPO, 1, 'a/b.png')),
  anchorOf(await ghDiffUrl('other', 'repo', 999, 'a/b.png')),
  'nothing but the path may go into the hash');
assert.notStrictEqual(
  anchorOf(await ghDiffUrl(OWNER, REPO, PR, 'a/b.png')),
  anchorOf(await ghDiffUrl(OWNER, REPO, PR, 'a/c.png')));

// ── The URL around it ───────────────────────────────────────────────────────

const url = await ghDiffUrl(OWNER, REPO, PR, 'a/b.png');
assert.ok(url.startsWith('https://github.com/' + OWNER + '/' + REPO + '/pull/' + PR + '/files#'),
  'the link must open the PR\'s Files tab on github.com, got ' + url);
assert.strictEqual(url.split('#').length, 2, 'exactly one fragment');
assert.ok(!/undefined|null|NaN/.test(url), 'no placeholder leaked into ' + url);

// The path reaches GitHub only as a hash, so a path full of URL metacharacters
// cannot break the link — and a future edit that helpfully appends the path
// fails here.
const odd = await ghDiffUrl(OWNER, REPO, PR, 'dir with space/a&b=c?d#e/é.png');
assert.strictEqual(anchorOf(odd), 'diff-' + sha256('dir with space/a&b=c?d#e/é.png'));
assert.strictEqual(odd.split('#').length, 2,
  'a path containing # must not reach the URL, got ' + odd);
assert.ok(!/\s/.test(odd), 'no raw path characters in ' + odd);

// ── The wiring ──────────────────────────────────────────────────────────────
// Everything above tests the pure region; the SPA reaches it from
// renderComparison, outside the region. Structural, like the wiring checks in
// test_image_urls.js and test_prefetch_policy.js.

const render = bodyOf('renderComparison');

// The container the link is written into has to exist in the info bar before
// the digest arrives, or there is nowhere to put it.
assert.ok(/id="gh-diff-link-container"/.test(render),
  'renderComparison must emit the placeholder the link lands in');

// The path is read once, up front, and passed in. Reading state.currentFile
// inside the callback instead is the defect the capture exists to prevent.
assert.ok(/var\s+linkPath\s*=\s*state\.currentFile\s*;/.test(render),
  'renderComparison must capture the path before awaiting the digest');
assert.ok(/ghDiffUrl\s*\(\s*owner\s*,\s*repo\s*,\s*prNumber\s*,\s*linkPath\s*\)/.test(render),
  'renderComparison must pass owner, repo, prNumber and the captured path');

// And re-checked on arrival: a navigation during the digest rebuilds the bar
// with a new container of the same id, so an unguarded write labels the file
// now on screen with the anchor of the one we left.
assert.ok(
  /if\s*\(\s*linkPath\s*!==\s*state\.currentFile\s*\)\s*return\s*;/.test(render) ||
  /if\s*\(\s*state\.currentFile\s*!==\s*linkPath\s*\)\s*return\s*;/.test(render),
  'renderComparison must drop a digest that arrived after the viewer moved on');

// A missing SubtleCrypto rejects; unhandled, that is a console error per file.
assert.ok(/\.catch\s*\(/.test(render),
  'renderComparison must handle a failed digest');

console.log('test_gh_diff_link: all checks passed');
}

main().catch(err => { console.error(err); process.exit(1); });
