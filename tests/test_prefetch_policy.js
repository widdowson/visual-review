// Exercises the prefetch policy that lives in static/index.html.
//
// The SPA is one file by design, so the policy is extracted from it here
// rather than copied: a test holding its own copy of the logic would keep
// passing after the real one changed. The markers are part of the contract —
// if they go missing this test fails rather than silently testing nothing.

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const runfiles = process.env.RUNFILES_DIR || '';
const htmlPath = runfiles
  ? path.join(runfiles, '_main', 'static', 'index.html')
  : path.join(__dirname, '..', 'static', 'index.html');

const html = fs.readFileSync(htmlPath, 'utf8');

const BEGIN = 'prefetch-policy:begin';
const END = 'prefetch-policy:end';
const from = html.indexOf(BEGIN);
const to = html.indexOf(END);
assert.ok(from > 0, 'static/index.html must contain the ' + BEGIN + ' marker');
assert.ok(to > from, 'static/index.html must contain the ' + END + ' marker after the begin marker');

const source = html.slice(html.indexOf('\n', from) + 1, html.lastIndexOf('\n', to));
assert.ok(/function computePrefetchPlan\s*\(/.test(source),
  'the marked region must define computePrefetchPlan');

// The region must be pure — no DOM, no network, no module state. If it grows
// a dependency on any of those it stops being testable here, so say so loudly.
// Comments are stripped first: the prose around the policy is allowed to use
// these words, and a purity check that trips on a comment gets deleted.
const code = source
  .replace(/\/\*[\s\S]*?\*\//g, ' ')
  .replace(/(^|[^:])\/\/.*$/gm, '$1');
const IMPURE = [
  [/\bdocument\b/, 'document'],
  [/\bwindow\b/, 'window'],
  [/\blocalStorage\b/, 'localStorage'],
  [/\bnew\s+Image\b/, 'new Image'],
  [/\bfetch\s*\(/, 'fetch('],
  [/\bstate\s*\./, 'state.'],
  [/\bsetTimeout\b/, 'setTimeout'],
  [/\bnew\s+Date\b/, 'new Date'],
  [/\bDate\s*\.\s*now\b/, 'Date.now'],
  [/\bMath\s*\.\s*random\b/, 'Math.random'],
];
for (const [pattern, label] of IMPURE) {
  assert.ok(!pattern.test(code),
    'the prefetch policy must stay pure; found "' + label + '" in the marked region');
}

const computePrefetchPlan = new Function(source + '\nreturn computePrefetchPlan;')();

// Defaults matching the SPA's constants, so each case states only what it varies.
function plan(overrides) {
  return computePrefetchPlan(Object.assign({
    count: 10, current: 0, cached: [], ahead: 1, behind: 1, radius: 2,
  }, overrides));
}

// ── What gets fetched ───────────────────────────────────────────────────────

// The whole point: sitting on a file warms the one below it.
assert.deepStrictEqual(plan({current: 4}).fetch, [5, 3],
  'next is fetched before previous');

// j/k both go somewhere, so the file above is warmed too — but second.
assert.deepStrictEqual(plan({current: 4, behind: 0}).fetch, [5]);
assert.deepStrictEqual(plan({current: 4, ahead: 0}).fetch, [3]);
assert.deepStrictEqual(plan({current: 4, ahead: 0, behind: 0}).fetch, []);

// Deeper windows stay next-first, nearest-first within each direction.
assert.deepStrictEqual(plan({current: 4, ahead: 2, behind: 2}).fetch, [5, 6, 3, 2]);

// Ends of the list have nothing beyond them.
assert.deepStrictEqual(plan({current: 0}).fetch, [1]);
assert.deepStrictEqual(plan({current: 9}).fetch, [8]);
assert.deepStrictEqual(plan({count: 1, current: 0}).fetch, [],
  'a single-file PR prefetches nothing');

// Nothing already in hand is fetched again.
assert.deepStrictEqual(plan({current: 4, cached: [5]}).fetch, [3]);
assert.deepStrictEqual(plan({current: 4, cached: [3, 5]}).fetch, []);

// No selection, or a nonsensical one, means no speculation at all.
assert.deepStrictEqual(plan({current: -1}), {fetch: [], evict: []});
assert.deepStrictEqual(plan({count: 0, current: 0}), {fetch: [], evict: []});
assert.deepStrictEqual(plan({current: 10}), {fetch: [], evict: []},
  'a current index past the end is not a window');

// ── What gets evicted ───────────────────────────────────────────────────────

// Decoded images are megabytes each, so the retained set is a sliding window
// rather than everything the reviewer has ever opened. radius 2 keeps 3..7
// around a selection at 5, and the prefetch window adds nothing beyond it here.
const far = plan({current: 5, cached: [0, 1, 3, 5, 7, 9]});
assert.deepStrictEqual(far.evict, [0, 1, 9], 'farthest first');
assert.ok(!far.evict.includes(5), 'the selected file is never evicted');

// Nothing is evicted just for being cached.
assert.deepStrictEqual(plan({current: 5, cached: [4, 5, 6]}).evict, []);

// A file the policy is about to prefetch is inside the window by construction,
// even where the radius alone would not reach it.
const narrow = plan({current: 5, radius: 0, cached: [4]});
assert.deepStrictEqual(narrow.fetch, [6]);
assert.deepStrictEqual(narrow.evict, [], 'radius 0 still keeps the prefetch window');

// Evictions and fetches never name the same index.
const overlap = plan({current: 5, radius: 1, cached: [2, 4, 5, 8]});
for (const i of overlap.fetch) {
  assert.ok(!overlap.evict.includes(i), 'index ' + i + ' is both fetched and evicted');
}

// Ties break towards the lower index: 2 and 8 are both three files away, and
// the one behind goes first. Without that the order would depend on however
// the caller happened to enumerate its cache.
assert.deepStrictEqual(plan({current: 5, radius: 1, cached: [2, 8]}).evict, [2, 8],
  'of two equally distant files the earlier goes first');
assert.deepStrictEqual(plan({current: 5, radius: 1, cached: [8, 2]}).evict, [2, 8],
  'eviction order is independent of input order');

// ── The function is pure ────────────────────────────────────────────────────

const cached = [0, 4, 9];
const snapshot = JSON.stringify(cached);
const first = plan({current: 5, cached: cached});
// Checked after one call, not two: a policy that reversed its input in place
// would look innocent when asked twice.
assert.strictEqual(JSON.stringify(cached), snapshot, 'input must not be mutated');
const second = plan({current: 5, cached: cached});
assert.strictEqual(JSON.stringify(cached), snapshot, 'input must not be mutated');
assert.deepStrictEqual(first, second, 'same input, same plan');

console.log('test_prefetch_policy: all checks passed');
