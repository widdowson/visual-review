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
const HARNESS_PATH = runfiles
  ? path.join(runfiles, '_main', 'tests', 'repo_page_harness.js')
  : path.join(__dirname, 'repo_page_harness.js');

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

    // These patterns pin the page's local variable *names* as well as the
    // argument order. The order is the point — swap two and every assertion
    // above still passes while the page reads its own state wrongly — and the
    // names are the incidental cost, so a rename is a failure of this check
    // rather than of the page. Each message below says so.
    const RENAME = ' (this check pins these local names; rename them here too)';
    assert.ok(/VRRepo\.summaryLine\(\s*rows\s*,\s*probed\s*,\s*loadError\s*,\s*listTruncated\s*\)/.test(page),
      'draw() must take the summary line from summaryLine, with the page state in order' + RENAME);
    assert.ok(/VRRepo\.markUncounted\(\s*rows\s*,/.test(page),
      'a failed load must mark the rows it left uncounted' + RENAME);
    assert.ok(/listTruncated\s*=\s*!!\s*\(?\s*(?:data\s*&&\s*)?data\.truncated/.test(page),
      "the response's top-level truncated flag must reach the page" + RENAME);
    // Round 4, Major 1: an error is decided by the key being there, never by
    // the string being non-empty. Scenario F drives the behaviour; this stops
    // the check reverting to `if (data.error)` and reading an empty-string
    // error as a good load.
    assert.ok(/'error'\s+in\s+data/.test(page),
      "a load failure must be decided by the presence of the error key, not its truthiness");
    assert.ok(!/if\s*\(\s*data\.error\s*\)/.test(page),
      'a bare truthiness test on data.error reads an empty-string error as success');

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

// ── The page, run ──────────────────────────────────────────────────────────
// Rounds 3-5. Everything above tests repo.js and the shape of the calls
// repo.html makes. Neither can see *which branch* runs, and every finding
// against this page's failure handling has been a branch finding: a message
// written where the next redraw erased it, a failed first load whose error one
// filter click replaced with "No open pull requests.", and an error string the
// page never recognised as one — each a positive claim about the question the
// page exists to answer, from a page that could not answer it.
//
// So these run the real inline script, over every way this endpoint's response
// can arrive. The reachable route to the second bug was a typo'd repo name:
// GitHub 404s, the page says so, and one click said the repository had no open
// PRs.
{
  const { drivePage } = require(HARNESS_PATH);

  // Driving the page must not disturb this file's own shim. An earlier harness
  // assigned global.document and left it assigned, so every assertion after
  // this block rendered through the harness's element() rather than the node()
  // documented beside them — harmless while the two agree and invisible the
  // moment they stop. repo.js is now evaluated inside the vm context, which is
  // also its real browser path, so nothing outside is touched; this is what
  // says so.
  const outerDocument = global.document;

  const ERR = { error: 'Pull request list failed: HTTP 404' };
  const ONE = {
    pulls: [{
      number: 1, title: 'PR 1', author: 'a', draft: false,
      html_url: 'https://github.com/o/r/pull/1', updated_at: '2026-09-15T11:00:00Z',
      head_ref: 'f', base_ref: 'main', labels: [],
      images: null, images_truncated: false, image_error: null,
    }],
    probed: false, truncated: false,
  };
  const EMPTY = { pulls: [], probed: true, truncated: false };
  const ONE_COUNTED = {
    pulls: [Object.assign({}, ONE.pulls[0], { images: 3 })],
    probed: true, truncated: false,
  };

  // The invariant, stated once and checked after every failure below: a page
  // that could not load must not tell anyone how many open PRs there are.
  const claimsEmptiness = p =>
    /No open pull requests|no image changes/.test(p.listText + ' ' + p.summary);

  const checks = [];

  // A — the first load fails outright.
  checks.push(drivePage([ERR, ERR]).then(page => {
    assert.ok(/HTTP 404/.test(page.listText), 'the failure is on the page');
    assert.ok(!claimsEmptiness(page), 'a failed load claims nothing about the count');
    page.toggleFilter();
    assert.ok(/HTTP 404/.test(page.listText),
      'the failure must survive a filter click, not be redrawn away');
    assert.ok(!claimsEmptiness(page),
      'one click after a failed load must not produce "No open pull requests."');
    page.toggleFilter().toggleFilter();
    assert.ok(/HTTP 404/.test(page.listText), 'nor any number of clicks');
  }));

  // B — the first load works and the probing pass fails. Round 2's case; it
  // stays fixed, and it is here so the two branches are checked together.
  checks.push(drivePage([ONE, ERR]).then(page => {
    assert.strictEqual(page.summary, 'Pull request list failed: HTTP 404');
    assert.strictEqual(page.summaryClass, 'summary-error');
    assert.deepStrictEqual(page.badges, ['check failed']);
    page.toggleFilter();
    assert.strictEqual(page.summary, 'Pull request list failed: HTTP 404',
      'a failure over rendered rows survives a filter click too');
    assert.deepStrictEqual(page.badges, ['check failed'],
      'and the rows it spoke for do not revert to "checking…"');
  }));

  // C — the fetch itself rejects, which reaches fail() by the other path.
  checks.push(drivePage([new Error('boom')]).then(page => {
    assert.ok(/boom/.test(page.listText));
    page.toggleFilter();
    assert.ok(/boom/.test(page.listText), 'a rejected fetch is state like any other');
    assert.ok(!claimsEmptiness(page));
  }));

  // D — zero rows and then a failure: no rows to keep, so it is case A again.
  checks.push(drivePage([EMPTY, ERR]).then(page => {
    page.toggleFilter();
    assert.ok(/HTTP 404/.test(page.listText));
    assert.ok(!claimsEmptiness(page));
  }));

  // E — the control, and the reason this cannot be fixed by never saying it: a
  // repository that really has no open PRs still says so.
  checks.push(drivePage([EMPTY, EMPTY]).then(page => {
    assert.ok(/No open pull requests/.test(page.listText),
      'an empty repository still reports itself empty');
    assert.ok(/No open pull requests/.test(page.summary));
  }));

  // F — round 4, Major 1. A read timeout on the list request stringifies to
  // the empty string all the way through httpx, so the server used to answer
  // {"error": "", "pulls": []} and `if (data.error)` read it as a good load of
  // an empty repository. The server no longer sends an empty string and the
  // page no longer decides by truthiness; either alone would fix the symptom,
  // and both are held because the contract has two sides.
  const EMPTY_ERROR = { error: '', pulls: [] };
  checks.push(drivePage([EMPTY_ERROR, EMPTY_ERROR]).then(page => {
    assert.ok(!claimsEmptiness(page),
      'an error with no message is still an error, not an empty repository');
    assert.ok(/Could not load/.test(page.listText),
      'and the page says something rather than nothing');
    page.toggleFilter();
    assert.ok(!claimsEmptiness(page), 'and a click does not turn it into one');
  }));

  // G — the same, arriving on the probing pass over a list already rendered.
  // This is the shape that made it a Major rather than a curiosity: it wiped
  // rows that were on screen and correct.
  checks.push(drivePage([ONE, EMPTY_ERROR]).then(page => {
    assert.ok(!claimsEmptiness(page), 'an empty-string error must not wipe the list');
    assert.strictEqual(page.badges.length, 1, 'the row that had rendered is still there');
    assert.deepStrictEqual(page.badges, ['check failed']);
  }));

  // H — round 4, Minor 1. The filter's listener is registered before the first
  // request is sent, so a click can land while the page still knows nothing.
  // It used to answer with "No open pull requests." over a repository that has
  // them, and then correct itself when the response arrived — a claim made
  // from the one state with no evidence behind it either way.
  checks.push(drivePage([ONE, ONE_COUNTED], { defer: true }).then(page => {
    assert.strictEqual(page.summary, 'Loading…', 'the served markup says this');
    assert.deepStrictEqual(page.requests, ['/api/o/r/pulls?probe=0'],
      'only the first request has been made — the page is genuinely mid-flight');
    page.toggleFilter();
    assert.ok(!claimsEmptiness(page),
      'a click before the first answer must claim nothing about the count');
    assert.strictEqual(page.summary, 'Loading…', 'it still says what it knows');
    return page.release().then(settled => {
      // The exact string, not an alternation. release() drains both passes, so
      // this is the fully-loaded page; an alternation tolerating "checking…"
      // would have passed on a page whose second request was never released.
      assert.strictEqual(settled.summary, '1 of 1 have image changes',
        'and the real answer arrives normally afterwards');
      assert.deepStrictEqual(settled.requests,
        ['/api/o/r/pulls?probe=0', '/api/o/r/pulls'], 'both passes ran');
    });
  }));

  // J — round 5, Minor 1. A body that is not an object at all. This endpoint
  // cannot produce one — every exit carries `error` or `pulls`, and anything
  // else in the chain answers something `r.json()` rejects, which is C — so
  // this is not a state the page can reach today. It is here because round 4
  // added `data &&` guards for it whose only effect was to swallow the
  // TypeError that used to reach the .catch as an honest failure and report
  // "No open pull requests." instead. The guards are gone; this is what says
  // the error path is what handles it.
  checks.push(drivePage([null, null]).then(page => {
    assert.ok(!claimsEmptiness(page),
      'a body this page cannot read is a failure, not an empty repository');
    assert.ok(/Could not load/.test(page.listText), 'and it says so');
    page.toggleFilter();
    assert.ok(!claimsEmptiness(page));
  }));

  // I — round 4, Minor 2. The cheap pass goes first. Reversed, the page shows
  // nothing until the whole fan-out has finished, which is the thing #39 says
  // the per-PR check must never cause; every other assertion here passes
  // either way.
  checks.push(drivePage([ONE, ONE]).then(page => {
    assert.deepStrictEqual(page.requests,
      ['/api/o/r/pulls?probe=0', '/api/o/r/pulls'],
      'the unprobed pass must be asked for first, so the list is never waiting on the counts');
  }));

  Promise.all(checks).then(
    () => {
      assert.strictEqual(global.document, outerDocument,
        'driving the page must not replace the outer realm\'s document');
      console.log('test_repo_page: page-driven checks passed');
    },
    err => { console.error(err); process.exit(1); });
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
