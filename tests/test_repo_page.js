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

const { verdictFor, summarize, summaryText, summaryLine, markUncounted, repoFromPath,
        viewerHref, pullsApiHref, relativeTime, renderList } = require(MODULE_PATH);

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

// ── Rendering ───────────────────────────────────────────────────────────────
//
// The rendering half is a third of this module and had no assertions at all in
// the first round, which is exactly where round 1 found a user-visible
// falsehood: the empty-state message said the opposite of what it was printed
// for. So it is driven here, through a DOM shim small enough to be obviously
// faithful — createElement, appendChild, textContent, className, href — rather
// than left to a browser run nobody repeats.

function shimDocument() {
  function node(tag) {
    return {
      tag: tag,
      className: '',
      children: [],
      _text: '',
      listeners: 0,
      get textContent() {
        // What a reader sees: this element's own text plus its children's.
        return this._text + this.children.map(c => c.textContent).join('');
      },
      set textContent(v) {
        // Assigning textContent replaces everything, as in a browser — which
        // is what renderList relies on to clear the container.
        this._text = String(v);
        this.children = [];
      },
      appendChild(child) { this.children.push(child); return child; },
      addEventListener() { this.listeners++; },
    };
  }
  // createElement, appendChild, textContent and className above are faithful.
  // `href` is not, and it is the only property here that is not: a browser's
  // HTMLAnchorElement.href is a reflected URL attribute whose getter resolves
  // against the document base, so a relative href set here reads back as
  // 'http://host/o/r/pr/1' there and as '/o/r/pr/1' below. Assigning it works
  // the same way in both, and the `=== undefined` half is faithful because
  // nothing ever assigns href to a <div> — so the assertions hold; a reader
  // should just not take the read-back string for what a browser would give.
  global.document = { createElement: node };
  return node('div');
}

const container = shimDocument();
const NOW = Date.parse('2026-09-15T12:00:00Z');
const row = (n, over) => Object.assign({
  number: n, title: 'PR ' + n, author: 'someone', draft: false,
  html_url: 'https://github.com/o/r/pull/' + n,
  updated_at: '2026-09-15T11:00:00Z',
  head_ref: 'feature-' + n, base_ref: 'main', labels: [],
  images: null, images_truncated: false, image_error: null,
}, over);

const find = (nodes, cls) => nodes.filter(n => n.className.indexOf(cls) >= 0);
const descend = node => [node].concat(...node.children.map(descend));

// A useful row is a link into the viewer; every other state is not a link at
// all, which is the distinction the whole page is for. Asserting the class
// alone would pass with every row an <a>.
renderList(container, 'o', 'r', [
  row(1, { images: 3 }),
  row(2, { images: 0 }),
  row(3, { image_error: 'HTTP 500' }),
  row(4, {}),
], { hideEmpty: false, nowMs: NOW });

const rendered = find(container.children, 'pr-row');
assert.strictEqual(rendered.length, 4);
assert.strictEqual(rendered[0].tag, 'a');
assert.strictEqual(rendered[0].href, '/o/r/pr/1');
for (const [i, state] of [[1, 'empty'], [2, 'unknown'], [3, 'checking']]) {
  assert.strictEqual(rendered[i].tag, 'div', 'a ' + state + ' row must not be a link');
  assert.strictEqual(rendered[i].href, undefined, 'a ' + state + ' row must have no href');
  assert.ok(rendered[i].className.includes('pr-' + state));
}

{
  // ── The failure path ───────────────────────────────────────────────────────
  // Round 2, Minor 1. The page used to write a load error straight into the
  // summary element, which draw() rewrites on every redraw — so one click on the
  // filter replaced "Pull request list failed: HTTP 502" with "1 open pull
  // request · checking which have image changes…", and the rows it was speaking
  // for sat on "checking…" for ever. The line is now derived from state.

  // An error wins the line and says so, and keeps winning it however many times
  // the page is redrawn.
  const failedLine = summaryLine([row(1, { images: 2 })], true, 'HTTP 502', false);
  assert.deepStrictEqual(failedLine, { text: 'HTTP 502', isError: true });
  assert.deepStrictEqual(
    summaryLine([row(1, { images: 2 })], true, 'HTTP 502', false), failedLine,
    'the line is a function of state, so a redraw cannot lose the error');

  // With no error it is the summary, unchanged.
  assert.deepStrictEqual(
    summaryLine([row(1, { images: 2 })], true, null, false),
    { text: '1 of 1 have image changes', isError: false });

  // The listing itself being cut short is said out loud rather than dropped.
  const cut = summaryLine([row(1, { images: 2 })], true, null, true);
  assert.ok(cut.text.startsWith('1 of 1 have image changes'));
  assert.ok(/list cut short/.test(cut.text), 'a truncated listing must be visible');
  assert.strictEqual(cut.isError, false);
  assert.ok(!/list cut short/.test(summaryLine([row(1)], true, null, false).text));

  // An error does not also carry the truncation note — the count it would
  // qualify is the count that failed.
  assert.strictEqual(summaryLine([row(1)], true, 'HTTP 502', true).text, 'HTTP 502');

  // And the rows: anything still waiting on a count is told the count is not
  // coming, rather than saying "checking…" for ever.
  const mixed = [row(1, { images: 2 }), row(2, {}), row(3, { image_error: 'its own' })];
  markUncounted(mixed, 'HTTP 502');
  assert.strictEqual(mixed[0].images, 2, 'a counted row is untouched');
  assert.strictEqual(mixed[0].image_error, null);
  assert.strictEqual(mixed[1].image_error, 'HTTP 502', 'an uncounted row is told');
  assert.strictEqual(mixed[2].image_error, 'its own', 'a row with its own error keeps it');
  assert.strictEqual(verdictFor(mixed[1]).state, 'unknown');
  assert.strictEqual(verdictFor(mixed[1]).label, 'check failed');
  assert.notStrictEqual(verdictFor(mixed[1]).label, 'checking…');

  // The state that matters most: after a failed probe pass nothing anywhere on
  // the page claims a PR has no images.
  const afterFailure = [row(1, {}), row(2, {}), row(3, {})];
  markUncounted(afterFailure, 'HTTP 502');
  for (const r of afterFailure) {
    assert.notStrictEqual(verdictFor(r).state, 'empty',
      'a failed probe must never render as "no images"');
  }

  // ── Wiring ─────────────────────────────────────────────────────────────────
  // The three assertions above are about repo.js. This one is about repo.html,
  // which has no test harness of its own: the page must take the line from
  // summaryLine rather than assigning to summaryEl directly, and must mark the
  // rows. Structural, like the adapter check in test_image_urls.js.
  {
    const fs = require('fs');
    const path = require('path');
    const runfiles = process.env.RUNFILES_DIR || '';
    const htmlPath = runfiles
      ? path.join(runfiles, '_main', 'static', 'repo.html')
      : path.join(__dirname, '..', 'static', 'repo.html');
    const page = fs.readFileSync(htmlPath, 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, ' ')
      .replace(/(^|[^:])\/\/.*$/gm, '$1');

    assert.ok(/VRRepo\.summaryLine\(\s*rows\s*,\s*probed\s*,\s*loadError\s*,\s*listTruncated\s*\)/.test(page),
      'draw() must take the summary line from summaryLine, with the page state in order');
    assert.ok(/VRRepo\.markUncounted\(\s*rows\s*,/.test(page),
      'a failed load must mark the rows it left uncounted');
    assert.ok(/listTruncated\s*=\s*!!\s*data\.truncated/.test(page),
      "the response's top-level truncated flag must reach the page");
    // The bug itself: nothing may write the error into the element, because the
    // next draw() overwrites it.
    const assignments = page.match(/summaryEl\.textContent\s*=\s*([^;]+);/g) || [];
    assert.ok(assignments.length > 0, 'the summary element must be written somewhere');
    for (const a of assignments) {
      assert.ok(/line\.text|''|""/.test(a),
        'the summary element may only be set from summaryLine or cleared, got: ' + a.trim());
    }
  }
}

// Every GitHub-supplied string reaches the page as text. A title that is
// markup stays one text node — nothing here parses it.
const nasty = '<img src=x onerror=alert(1)>';
renderList(container, 'o', 'r', [row(9, { images: 1, title: nasty, labels: ['<b>lgtm</b>'] })],
  { hideEmpty: false, nowMs: NOW });
const titleNode = find(descend(container), 'pr-title')[0];
assert.strictEqual(titleNode.textContent, nasty, 'the title is text, not markup');
assert.strictEqual(titleNode.children.length, 0, 'setting a title must create no elements');
assert.ok(find(descend(container), 'pr-label')[0].textContent === '<b>lgtm</b>');

// The empty state, which is where round 1's Major was. It can arise in exactly
// one way — the filter hid every row — so the message must say that and not
// its opposite.
renderList(container, 'o', 'r', [row(1, { images: 0 }), row(2, { images: 0 })],
  { hideEmpty: true, nowMs: NOW });
const none = find(container.children, 'pr-none');
assert.strictEqual(none.length, 1, 'a list filtered down to nothing says something');
assert.strictEqual(find(container.children, 'pr-row').length, 0);
assert.strictEqual(none[0].textContent, 'Every open pull request here has no image changes.');
// It must not contradict the summary line rendered directly above it.
assert.strictEqual(summaryText(summarize([row(1, { images: 0 }), row(2, { images: 0 })]), true),
  '0 of 2 have image changes');

// An empty repo is a different sentence.
renderList(container, 'o', 'r', [], { hideEmpty: false, nowMs: NOW });
assert.strictEqual(find(container.children, 'pr-none')[0].textContent, 'No open pull requests.');

// The filter hides "no images" rows and nothing else — a row nobody could
// check is not a row with nothing in it, so it stays visible.
renderList(container, 'o', 'r', [
  row(1, { images: 2 }),
  row(2, { images: 0 }),
  row(3, { image_error: 'HTTP 500' }),
  row(4, {}),
], { hideEmpty: true, nowMs: NOW });
const kept = find(container.children, 'pr-row');
assert.deepStrictEqual(kept.map(n => n.href), ['/o/r/pr/1', undefined, undefined]);
assert.ok(kept[1].className.includes('pr-unknown'));
assert.ok(kept[2].className.includes('pr-checking'));

console.log('test_repo_page: all checks passed');
