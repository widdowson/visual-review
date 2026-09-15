// Exercises the prefetch policy that lives in static/index.html.
//
// The SPA is one file by design, so the policy is extracted from it here
// rather than copied: a test holding its own copy of the logic would keep
// passing after the real one changed. The markers are part of the contract —
// if they go missing this test fails rather than silently testing nothing.

const assert = require('assert');
const { extract, bodyOf } = require('./spa_source');

const computePrefetchPlan = extract('prefetch-policy', 'computePrefetchPlan');

// ── The shipped defaults ────────────────────────────────────────────────────
// Asserted because the policy below is exercised with explicit arguments, so
// every case here would still pass with the feature switched off in the SPA.
// The tuning lives inside the same marked region and is read by evaluating it,
// not by matching text — an earlier version grepped for `var NAME = <n>;` and
// a commented-out previous value sitting above a live one read as the live one.
// Each assertion is a floor on the feature existing, not an opinion about its
// tuning: raising any of these is a judgement call and must not fail here.

const tuning = extract('prefetch-policy', 'PREFETCH_TUNING');
assert.strictEqual(typeof tuning, 'object', 'the region must export a PREFETCH_TUNING object');
assert.deepStrictEqual(
  Object.keys(tuning).sort(), ['ahead', 'behind', 'cacheRadius', 'delayMs'],
  'PREFETCH_TUNING gained or lost a key; the assertions below need updating');
for (const [k, v] of Object.entries(tuning)) {
  assert.ok(Number.isInteger(v), 'PREFETCH_TUNING.' + k + ' must be an integer, got ' + v);
}

assert.ok(tuning.ahead >= 1, 'the SPA must prefetch at least the next file');
assert.ok(tuning.behind >= 1, 'the SPA must prefetch at least the previous file');
assert.ok(tuning.delayMs > 0, 'prefetching must be debounced');
// Only that the retained window reaches past the current file. Deliberately
// not tied to the prefetch depth: the policy keeps `want` in `keep` whatever
// the radius, and a case below pins that at radius 0.
assert.ok(tuning.cacheRadius >= 1, 'the retained window must extend beyond the current file');

// The SPA must actually feed the tuning to the policy; the plan cases below
// all pass explicit arguments, so none of them would notice a hard-coded 0.
const runPrefetchBody = bodyOf('runPrefetch');
for (const key of ['ahead', 'behind', 'cacheRadius']) {
  assert.ok(new RegExp('PREFETCH_TUNING\\s*\\.\\s*' + key + '\\b').test(runPrefetchBody),
    'runPrefetch must pass PREFETCH_TUNING.' + key + ' to the policy');
}
assert.ok(/PREFETCH_TUNING\s*\.\s*delayMs\b/.test(bodyOf('schedulePrefetch')),
  'schedulePrefetch must use PREFETCH_TUNING.delayMs');

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

// ── Wiring ──────────────────────────────────────────────────────────────────
// A structural check, not a behavioural one: the policy above is pure, so
// every case in this file still passes if the SPA computes a plan and then
// ignores it. There is no browser harness in this repo to assert the real
// thing, so assert instead that each half of the plan is consumed and that
// prefetching is armed from the loader's ready path.

assert.ok(/plan\.fetch\b[\s\S]*startPrefetch\s*\(/.test(runPrefetchBody),
  'runPrefetch must start a prefetch for each index the plan asks for');
assert.ok(/plan\.evict\b[\s\S]*dropCached\s*\(/.test(runPrefetchBody),
  'runPrefetch must drop each index the plan evicts');

assert.ok(/schedulePrefetch\s*\(\s*\)/.test(bodyOf('loadImagePair')),
  'loadImagePair must arm the prefetch once the current pair is ready');
assert.ok(/setTimeout\s*\(\s*runPrefetch\s*,/.test(bodyOf('schedulePrefetch')),
  'schedulePrefetch must arm runPrefetch itself, not some other callback');
assert.ok(/cancelPrefetchTimer\s*\(\s*\)/.test(bodyOf('selectFile')),
  'selectFile must disarm any armed prefetch before loading the new pair');
assert.ok(/clearTimeout\s*\(/.test(bodyOf('cancelPrefetchTimer')),
  'cancelPrefetchTimer must actually clear the timer');

console.log('test_prefetch_policy: all checks passed');
