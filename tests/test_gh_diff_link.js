// Exercises ghDiffUrl, which lives in static/index.html: the link from a file
// in the viewer to that same file's diff on the GitHub PR (issue #15).
//
// The anchor format is GitHub's, not ours, and it is the entire feature — get
// the hash wrong and every link lands at the top of the Files tab, which looks
// exactly like it worked. So the digest is checked against node's sha256
// rather than against ghDiffUrl's own output, and the one hard-coded pair
// below is sourced externally rather than read off our implementation.

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

// This pair — `src/index.js` and its anchor — comes from GitHub Community
// Discussion #43908, answered by a community member in January 2023, not from
// GitHub documentation; the format is undocumented. It is worth pinning
// precisely because of that: a second community answer describes the anchor as
// an *md5* of the path, so the scheme has evidently changed at least once.
//
// Note what this assertion can and cannot do. It freezes our output against
// an externally sourced datum, so an edit here that quietly changes the scheme
// is red. It cannot detect a change on GitHub's side — nothing in this repo
// observes github.com — so if GitHub moves again, this test stays green and
// the links go quietly wrong. Only a human clicking one finds that.
assert.strictEqual(
  await ghDiffUrl('pallets', 'flask', 7, 'src/index.js'),
  'https://github.com/pallets/flask/pull/7/files' +
    '#diff-bfe9874d239014961b1ae4e89875a6155667db834a410aaaa2ebe3cf89820556',
  'the anchor must be diff- + sha256 hex of the file path, on the /files tab');

// ── The digest ──────────────────────────────────────────────────────────────

// Checked against node's own sha256 over the path's exact bytes. That pins
// hashing the wrong bytes in general — a trailing newline, the owner or repo
// mixed in, a different algorithm — without a case per way of being wrong.
for (const path of [
  'django/apps/proofing/tests/visual/cuj_proofing_06_quota_chips.bmp',
  'a.png',
  'dir/sub/file name with spaces.png',
  'accénts/café.bmp',
  // Mixed case, because the rest of this list is lower-case and a
  // `path.toLowerCase()` regression would otherwise be invisible. The viewer
  // renders such paths: nothing downcases a filename on the way in.
  'django/apps/Proofing/tests/Visual/CUJ_Quota_Chips.BMP',
]) {
  const url = await ghDiffUrl(OWNER, REPO, PR, path);
  assert.strictEqual(anchorOf(url), 'diff-' + sha256(path),
    'the anchor for ' + path + ' must be sha256 of exactly that path');
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

// The path reaches GitHub only as a hash, so a path full of URL metacharacters
// cannot break the link — and a future edit that helpfully appends the path
// fails here.
const odd = await ghDiffUrl(OWNER, REPO, PR, 'dir with space/a&b=c?d#e/é.png');
assert.strictEqual(anchorOf(odd), 'diff-' + sha256('dir with space/a&b=c?d#e/é.png'));
assert.strictEqual(odd.split('#').length, 2,
  'a path containing # must not reach the URL, got ' + odd);
assert.ok(!/\s/.test(odd), 'no raw path characters in ' + odd);

// ── The wiring ──────────────────────────────────────────────────────────────
// Everything above tests the region; the SPA reaches it from renderComparison,
// outside the region. Structural, like the wiring checks in test_image_urls.js
// and test_prefetch_policy.js.
//
// Read the limit of that before adding to it. These match source text, so they
// pin a *shape*: that a call and a guard exist, in a given order. They cannot
// reach whether a statement executes, and they see one occurrence of each
// thing they name. Measured on this head, all suite-green: a guard wrapped in
// `if (false)`, a guard whose captured local is reassigned just above it, a
// guard inside a nested function nobody calls, an href written from something
// other than ghUrl while `escAttr(ghUrl)` still appears somewhere, a *second*
// unguarded write through another identifier, and the placeholder appended
// last from a variable declared early — which the ordering check below reads
// as correct because it compares source positions, not execution. Every one of
// those needs a browser driving the race to catch, which is the harness tracked
// in #21; that issue already names the category ("a call that is present but
// never executed"). So treat this block as a floor under the obvious
// regressions, not as coverage.
//
// Two assertions are brittle by choice: a rewrite to createElement/setAttribute
// is a strictly safer write and fails the writeAt anchor, and the capture below
// takes the *first* `X = state.currentFile;` in the function, so an unrelated
// earlier one fails with a message blaming the wrong line. Both fail loudly
// rather than passing quietly, which is the way round it should be. Do not
// replace any of this with a real lexer; add a driven test instead.

const render = bodyOf('renderComparison');
const at = (needle, what) => {
  const i = render.indexOf(needle);
  assert.ok(i >= 0, 'renderComparison must ' + what);
  return i;
};

// Where the link lands, which is the one thing issue #15 specifies: "right
// after the filename and right before Current:". Presence alone does not pin
// it — the placeholder moved to the end of the bar keeps a presence check
// green while putting the link after the dimensions.
const barAt = at('var infoHtml', 'build the info bar');
const placeholderAt = at('id="gh-diff-link-container"', 'emit the placeholder the link lands in');
const baseAt = at('Base:', 'show the base dimensions');
const currentAt = at('Current:', 'show the current dimensions');
assert.ok(barAt < placeholderAt && placeholderAt < baseAt && placeholderAt < currentAt,
  'the link must sit after the filename and before the Base/Current fields');

// The path is read once, up front, and passed in. Reading state.currentFile
// inside the callback instead is the defect the capture exists to prevent.
const capture = /(?:var|let|const)\s+(\w+)\s*=\s*state\.currentFile\s*;/.exec(render);
assert.ok(capture,
  'renderComparison must capture state.currentFile into a local before awaiting the digest');
const pathVar = capture[1];
assert.ok(
  new RegExp('ghDiffUrl\\s*\\(\\s*owner\\s*,\\s*repo\\s*,\\s*prNumber\\s*,\\s*' + pathVar + '\\s*\\)')
    .test(render),
  'renderComparison must pass owner, repo, prNumber and the captured path');

// And re-checked on arrival: a navigation during the digest rebuilds the bar
// with a new container of the same id, so an unguarded write labels the file
// now on screen with the anchor of the one we left.
//
// Three positions, not one, because two reorderings each restore the bug while
// leaving the guard in the file. Below the write, it guards nothing. Hoisted
// above the digest call, it compares a local against the field it was assigned
// from one statement earlier, so it can never fire — which is the same bug and
// the harder one to see, since the guard still reads correctly in isolation.
const guardAt = render.search(new RegExp(
  'if\\s*\\(\\s*(?:' + pathVar + '\\s*!==\\s*state\\.currentFile' +
  '|state\\.currentFile\\s*!==\\s*' + pathVar + ')\\s*\\)\\s*return\\s*;'));
// Anchored to the digest call, not to the function's first `.then(`. Those are
// the same occurrence today, but any earlier promise chain above this block — a
// fetch, an img.decode() — would silently make a bare first-indexOf stop
// constraining the guard's scope, and the hoisted-guard mutant would pass again.
const callAt = at('ghDiffUrl(', 'call ghDiffUrl');
const thenAt = render.indexOf('.then(', callAt);
assert.ok(thenAt > callAt, 'renderComparison must handle the digest asynchronously');
const writeAt = at('container.innerHTML', 'write the link into the container');
assert.ok(guardAt >= 0,
  'renderComparison must drop a digest that arrived after the viewer moved on');
assert.ok(thenAt < guardAt,
  'the staleness guard must sit inside the digest callback: before it, the two ' +
  'sides cannot differ and the guard never fires');
assert.ok(guardAt < writeAt,
  'the staleness guard must run before the link is written, not after it');

// The href is interpolated into an attribute, so it goes through escAttr. Not
// exploitable as things stand — every component is encodeURIComponent'd and
// the hash is hex — but encodeURIComponent leaves a single quote alone, so
// without escAttr the write's safety rests on the attribute's quoting alone.
assert.ok(/escAttr\s*\(\s*ghUrl\s*\)/.test(render),
  'the url must be escaped before it is interpolated into the href');

// A missing SubtleCrypto rejects; unhandled, that is a console error per file.
assert.ok(/\.catch\s*\(/.test(render),
  'renderComparison must handle a failed digest');

console.log('test_gh_diff_link: all checks passed');
}

main().catch(err => { console.error(err); process.exit(1); });
