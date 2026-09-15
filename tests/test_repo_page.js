// Exercises the repo page's logic, which lives in static/repo.js.
//
// The page's job is to say which open PRs are worth opening in the viewer, so
// what is tested here is the verdict: the four states it can report, and in
// particular that the two it cannot answer — not checked yet, and check
// failed — never collapse into "no images". A PR wrongly listed as empty is a
// PR nobody opens, which is the mistake this page exists to prevent.

const assert = require('assert');
const path = require('path');

const runfiles = process.env.RUNFILES_DIR || '';
const MODULE_PATH = runfiles
  ? path.join(runfiles, '_main', 'static', 'repo.js')
  : path.join(__dirname, '..', 'static', 'repo.js');

const { verdictFor, summarize, summaryText, repoFromPath, viewerHref, pullsApiHref, relativeTime } =
  require(MODULE_PATH);

// ── The four verdicts ───────────────────────────────────────────────────────

const withImages = verdictFor({ images: 3, images_truncated: false });
assert.strictEqual(withImages.state, 'images');
assert.strictEqual(withImages.label, '3 images');
assert.strictEqual(withImages.useful, true);

assert.strictEqual(verdictFor({ images: 1, images_truncated: false }).label, '1 image',
  'one image is not "1 images"');

const empty = verdictFor({ images: 0, images_truncated: false });
assert.strictEqual(empty.state, 'empty');
assert.strictEqual(empty.label, 'no images');
assert.strictEqual(empty.useful, false);

// Not probed yet. The server sends `images: null` for every row in its first,
// cheap answer, and a page that read that as 0 would flash "no images" against
// every PR before the counts arrived.
for (const notProbed of [{ images: null }, {}, { images: undefined }]) {
  const verdict = verdictFor(notProbed);
  assert.strictEqual(verdict.state, 'checking',
    'an uncounted row is "checking", not empty: ' + JSON.stringify(notProbed));
  assert.strictEqual(verdict.useful, false);
}

// Check failed. This is the one that matters: GitHub said no, so nothing is
// known about this PR's images, and saying "no images" would be a claim
// nobody made.
const failed = verdictFor({ images: null, image_error: 'Files request failed: HTTP 500' });
assert.strictEqual(failed.state, 'unknown');
assert.strictEqual(failed.label, 'check failed');
assert.strictEqual(failed.useful, false);
assert.ok(failed.detail.includes('500'), 'the reason is carried through for the tooltip');

// An error wins over a count, so a row carrying both is never presented as a
// settled answer.
assert.strictEqual(verdictFor({ images: 0, image_error: 'boom' }).state, 'unknown');

// ── A truncated walk is not evidence of absence ─────────────────────────────

// Zero images after a walk that ran out of pages means "we stopped looking",
// not "there are none".
const cut = verdictFor({ images: 0, images_truncated: true });
assert.strictEqual(cut.state, 'unknown');
assert.strictEqual(cut.useful, false);
assert.notStrictEqual(cut.label, 'no images');

// With images found, a truncated walk gives a floor rather than a total.
const floor = verdictFor({ images: 40, images_truncated: true });
assert.strictEqual(floor.state, 'images');
assert.strictEqual(floor.label, '40+ images');
assert.strictEqual(floor.useful, true);

// ── The summary line ────────────────────────────────────────────────────────

const rows = [
  { images: 3 },                                    // useful
  { images: 0 },                                    // empty
  { images: 0 },                                    // empty
  { images: null, image_error: 'HTTP 500' },        // unknown
];
assert.deepStrictEqual(summarize(rows),
  { total: 4, useful: 1, empty: 2, unknown: 1, checking: 0 });

assert.strictEqual(summaryText(summarize(rows), true),
  '1 of 4 have image changes · 1 could not be checked');
assert.strictEqual(summaryText(summarize([{ images: 2 }, { images: 0 }]), true),
  '1 of 2 have image changes',
  'nothing is said about failures when there were none');
assert.strictEqual(summaryText(summarize([]), true), 'No open pull requests.');

// While counts are outstanding the line says so rather than reporting a total
// that is still moving — including when the server says it probed but a row
// came back uncounted.
assert.ok(summaryText(summarize([{ images: null }, { images: 1 }]), false).includes('checking'));
assert.ok(summaryText(summarize([{ images: null }, { images: 1 }]), true).includes('checking'));

// ── URLs ────────────────────────────────────────────────────────────────────

assert.deepStrictEqual(repoFromPath('/widdowson/apwphotos-appv2'),
  { owner: 'widdowson', repo: 'apwphotos-appv2' });
assert.deepStrictEqual(repoFromPath('/widdowson/apwphotos-appv2/'),
  { owner: 'widdowson', repo: 'apwphotos-appv2' }, 'a trailing slash is not a third segment');
assert.strictEqual(repoFromPath('/onlyone'), null);
assert.strictEqual(repoFromPath('/a/b/pr/1'), null);
assert.strictEqual(repoFromPath('/'), null);
assert.strictEqual(repoFromPath(''), null);

assert.strictEqual(viewerHref('widdowson', 'visual-review', 38),
  '/widdowson/visual-review/pr/38');
assert.strictEqual(pullsApiHref('o', 'r', true), '/api/o/r/pulls');
assert.strictEqual(pullsApiHref('o', 'r', false), '/api/o/r/pulls?probe=0',
  'the first request is the cheap one');

// A repo or owner name is a path segment, not a path.
assert.strictEqual(viewerHref('a/b', 'c', 1), '/a%2Fb/c/pr/1');

// ── Relative times ──────────────────────────────────────────────────────────

const now = Date.parse('2026-09-15T12:00:00Z');
assert.strictEqual(relativeTime('2026-09-15T11:59:30Z', now), '30s ago');
assert.strictEqual(relativeTime('2026-09-15T11:30:00Z', now), '30m ago');
assert.strictEqual(relativeTime('2026-09-15T09:00:00Z', now), '3h ago');
assert.strictEqual(relativeTime('2026-09-12T12:00:00Z', now), '3d ago');
assert.strictEqual(relativeTime('2026-06-15T12:00:00Z', now), '3mo ago');
assert.strictEqual(relativeTime('2026-09-15T12:00:30Z', now), 'just now',
  'a clock skew must not render as a negative age');
assert.strictEqual(relativeTime('not a date', now), '',
  'an unparseable timestamp says nothing rather than "NaN ago"');
assert.strictEqual(relativeTime(undefined, now), '');

console.log('test_repo_page: all checks passed');
