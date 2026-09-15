// Exercises the deep-link naming that lives in static/index.html: which name
// a file is written into the URL hash under, and which file a hash names.
//
// The regression this file exists for (#20): both halves used the basename
// alone, so a PR carrying one basename in two directories could not deep-link
// the second of them. Selecting it wrote `#name.bmp`, the write fired
// hashchange, the read scanned from the top, and the selection bounced to the
// first file sharing that basename. These baselines repeat names across
// per-app directories, so it was the normal case rather than an exotic one.

const assert = require('assert');
const { extract, bodyOf } = require('./spa_source');

const basenameOf = extract('hash-target', 'basenameOf');
const hashTargetFor = extract('hash-target', 'hashTargetFor');
const resolveHashTarget = extract('hash-target', 'resolveHashTarget');
const encodeHashTarget = extract('hash-target', 'encodeHashTarget');

const files = paths => paths.map(p => ({path: p}));

// The payload the issue reproduces with: files 0 and 2 share a basename.
const COLLIDING = files([
  'django/apps/home/tests/visual/cuj_01_login.bmp',
  'django/apps/home/tests/visual/cuj_02_dashboard.bmp',
  'django/apps/shoots/tests/visual/cuj_01_login.bmp',
]);

// ── basenameOf ──────────────────────────────────────────────────────────────

assert.strictEqual(basenameOf('a/b/c.png'), 'c.png');
assert.strictEqual(basenameOf('c.png'), 'c.png', 'a bare filename is its own basename');
assert.strictEqual(basenameOf('a/b/'), '', 'a trailing separator leaves nothing after it');

// ── What gets written ───────────────────────────────────────────────────────

// The short form stays the common case: a basename no other file shares is
// unambiguous, and these links are shared by hand.
assert.strictEqual(
  hashTargetFor(COLLIDING[1].path, COLLIDING), 'cuj_02_dashboard.bmp',
  'a basename unique in the PR must still be written short');

// The long form appears exactly where the short one would be a lie — for both
// colliding files, not just the later one.
for (const i of [0, 2]) {
  assert.strictEqual(
    hashTargetFor(COLLIDING[i].path, COLLIDING), COLLIDING[i].path,
    'a basename shared with another file must be written as the whole path');
}

// The file is not a collision with itself.
assert.strictEqual(hashTargetFor('a/b.png', files(['a/b.png'])), 'b.png');

// A path absent from the list is still named, since nothing in the list
// shadows it.
assert.strictEqual(hashTargetFor('x/y.png', COLLIDING), 'y.png');
assert.strictEqual(hashTargetFor('x/y.png', []), 'y.png');

// ── What a written name resolves back to ────────────────────────────────────

// The fix: a whole path is matched before any basename. Under the basename
// pass alone this answers 0, which is the bug.
assert.strictEqual(
  resolveHashTarget('django/apps/shoots/tests/visual/cuj_01_login.bmp', COLLIDING), 2,
  'an exact path must resolve to that file, not to one sharing its basename');
assert.strictEqual(resolveHashTarget(COLLIDING[0].path, COLLIDING), 0);

// The basename fallback is kept, which is what makes every link shared before
// this change keep working. An ambiguous one still lands on the first match —
// there is nothing else it could mean.
assert.strictEqual(resolveHashTarget('cuj_02_dashboard.bmp', COLLIDING), 1);
assert.strictEqual(resolveHashTarget('cuj_01_login.bmp', COLLIDING), 0);

// An exact path is preferred even when it is *later* than a basename match,
// which is the whole ordering question; a pass that returned the earliest
// match of either kind would answer 0 here.
assert.strictEqual(
  resolveHashTarget('b/name.png', files(['a/name.png', 'b/name.png'])), 1);

assert.strictEqual(resolveHashTarget('nothing.png', COLLIDING), -1);
assert.strictEqual(resolveHashTarget('cuj_01_login.bmp', []), -1);
assert.strictEqual(resolveHashTarget('', COLLIDING), -1, 'an empty target names nothing');

// ── Encoding ────────────────────────────────────────────────────────────────

// Separators survive as separators: a fragment may hold a literal `/`, and a
// whole path percent-encoded into %2F is unreadable in the URL bar and in a
// pasted link, which is what these are for.
assert.strictEqual(encodeHashTarget('a/b/c.png'), 'a/b/c.png');

// Everything else is encoded, including the `#` that would otherwise truncate
// the fragment and the `%` that would make it un-decodable.
assert.strictEqual(encodeHashTarget('dir with space/a#b%c.png'),
  'dir%20with%20space/a%23b%25c.png');

// A unique basename is written exactly as it was before this change.
assert.strictEqual(encodeHashTarget('cuj_01_login.bmp'), 'cuj_01_login.bmp');

// ── The round trip, which is the regression ─────────────────────────────────
//
// Selecting a file writes a hash; the browser fires hashchange; the read
// resolves it back to a file. The SPA's own guard against re-selecting the
// current file only holds if that round trip is the identity, so this is the
// assertion the bug fails: clicking file 2 used to land on file 0.

function roundTrip(path, images) {
  const written = encodeHashTarget(hashTargetFor(path, images));
  return resolveHashTarget(decodeURIComponent(written), images);
}

for (let i = 0; i < COLLIDING.length; i++) {
  assert.strictEqual(roundTrip(COLLIDING[i].path, COLLIDING), i,
    'selecting ' + COLLIDING[i].path + ' must resolve back to itself');
}

// A file at the repository root collides with a nested one carrying its name,
// and there its own path *is* that ambiguous basename — so nothing can be
// written to disambiguate it and the read order is the only thing that can.
// Under a basename-first pass this resolves to the nested file instead.
const ROOT_COLLIDING = files(['dir/name.png', 'name.png']);
for (let i = 0; i < ROOT_COLLIDING.length; i++) {
  assert.strictEqual(roundTrip(ROOT_COLLIDING[i].path, ROOT_COLLIDING), i,
    'selecting ' + ROOT_COLLIDING[i].path + ' must resolve back to itself');
}
assert.strictEqual(resolveHashTarget('name.png', ROOT_COLLIDING), 1,
  'an exact path must win over an earlier basename match');

// Not only for names that collide, and not only for paths that are tidy.
const ODD = files(['a/é b.png', 'x/y/a#b.png', 'z/é b.png']);
for (let i = 0; i < ODD.length; i++) {
  assert.strictEqual(roundTrip(ODD[i].path, ODD), i,
    'selecting ' + ODD[i].path + ' must resolve back to itself');
}

// ── The wiring, driven ──────────────────────────────────────────────────────
//
// Everything above tests the extracted region, and every one of those
// assertions passes with either DOM-side function left on its own basename
// loop — which is precisely the state this PR found the file in. So the call
// sites need covering too, and they touch the DOM, so they cannot be pulled
// into the pure region.
//
// They can still be *run*. bodyOf hands over their source, and `new Function`
// supplies every name they close over as a parameter, so the stubs below stand
// in for the page. What that buys over matching their text is the difference
// between a call being written and its answer being used: round 1 of this PR's
// review found ten edits that satisfied a set of regexes here while leaving
// the page broken, two of them re-landing #20 exactly — an added second write
// after the correct one, and a re-scan after the correct resolve. Both are red
// below. The regexes are gone rather than kept alongside: `.split('/')` banned
// one spelling of an operation `lastIndexOf('/')` performs just as well, and a
// check that cannot fail is worse than no check, because it reads like one.
//
// The cost is that these stubs have to keep up with the SPA. A name either
// function starts closing over and this file does not supply is a
// ReferenceError naming it, which is a loud failure pointing here — the same
// trade, and the same way round, as the brace walk in spa_source.js. That
// holds only for code the driver actually reaches, which is why the sidebar
// stub below returns rows rather than an empty list: with nothing to iterate,
// the one callback in selectFile never ran, and a name unstubbed *there* was
// silently fine while this paragraph promised otherwise.

function driver(opts) {
  const state = {
    images: opts.images,
    currentFile: opts.currentFile || null,
    lastDirection: null,
    imageCache: {},
  };

  // A browser stores the fragment with its '#', which is what the read side
  // strips back off; writing '' clears it. Modelling that is what makes the
  // write and the read below a real round trip rather than two half-tests.
  let stored = opts.hash === undefined ? '' : opts.hash;
  const location = {
    get hash() { return stored; },
    set hash(v) { stored = v === '' ? '' : '#' + v; },
  };

  const noop = () => {};
  const element = () => ({
    style: {}, innerHTML: '',
    classList: {add: noop, remove: noop, toggle: noop},
  });

  // One sidebar row per file, each answering data-path and recording whether
  // it was marked active, so the loop that highlights the selection runs and
  // is assertable rather than iterating over nothing.
  const rows = opts.images.map(function(img) {
    const row = {active: null};
    row.getAttribute = name => (name === 'data-path' ? img.path : null);
    row.classList = {toggle: (cls, on) => { if (cls === 'active') row.active = on; }};
    return row;
  });
  const fileList = {querySelectorAll: () => rows};

  const loaded = [];
  const selectFile = new Function(
    'state', 'location', 'cancelPrefetchTimer', 'fileList', 'modeToolbar',
    'viewport', 'imageInfo', 'loadImagePair', 'loadComments',
    'hashTargetFor', 'encodeHashTarget',
    bodyOf('selectFile') + '\nreturn selectFile;')(
      state, location, noop, fileList, element(), element(), element(),
      p => loaded.push(p), noop, hashTargetFor, encodeHashTarget);

  // Wrapped rather than replaced: the read side has to reach the real
  // selectFile for the round trip to mean anything, and the arguments it was
  // reached with are themselves the assertion for skipHash.
  const selected = [];
  const spy = (path, skipHash, direction) => {
    selected.push({path: path, skipHash: skipHash});
    return selectFile(path, skipHash, direction);
  };
  const selectFileFromHash = new Function(
    'state', 'location', 'selectFile', 'resolveHashTarget',
    bodyOf('selectFileFromHash') + '\nreturn selectFileFromHash;')(
      state, location, spy, resolveHashTarget);

  return {
    state: state, selected: selected, loaded: loaded, rows: rows,
    selectFile: selectFile, selectFileFromHash: selectFileFromHash,
    hash: () => stored,
  };
}

// ── Writing ─────────────────────────────────────────────────────────────────

// Selecting a colliding file leaves a hash that names it, and loads it.
{
  const d = driver({images: COLLIDING, currentFile: COLLIDING[0].path});
  d.selectFile(COLLIDING[2].path);
  assert.strictEqual(d.hash(), '#' + COLLIDING[2].path,
    'selecting a file whose basename collides must write its whole path');
  assert.deepStrictEqual(d.loaded, [COLLIDING[2].path],
    'and must load the file that was asked for');
  assert.strictEqual(d.state.currentFile, COLLIDING[2].path);
  assert.deepStrictEqual(d.rows.map(r => r.active), [false, false, true],
    'and must mark that row active and no other — the two colliding rows are '
    + 'told apart by their path, which is the only thing that distinguishes them');
}

// The write must encode. No other write case can see this: every path in
// COLLIDING and ROOT_COLLIDING is ASCII, where encodeHashTarget is the
// identity, and the percent-encoding case further down is a read. Round 2 of
// this PR's review measured the gap — dropping encodeHashTarget from the write
// was green — and it is the write half of the feature. A raw `%` reaching the
// fragment is a URIError on the next read, on a hash the app wrote itself.
{
  const d = driver({images: files(['dir/a b%c.png'])});
  d.selectFile('dir/a b%c.png');
  assert.strictEqual(d.hash(), '#a%20b%25c.png',
    'the written hash must be encoded, not the raw name');
}

// A collision that also needs encoding, with its partner away from index 0.
// This is the only write case that composes all three of the things the call
// site does, and it takes three mutations that each survive every other case:
// the two functions nested the wrong way round — which encodes first, so the
// basename matches nothing and the collision is never found — a collision
// search that stops short of the partner, and no encode at all. Round 3 of
// this PR's review measured the first two, and the first is an ordinary
// refactoring slip whose consequence is #20 itself.
{
  const WIDE = files(['z/other.png', 'a/cuj 01 login.bmp', 'b/cuj 01 login.bmp']);
  const d = driver({images: WIDE});
  d.selectFile('b/cuj 01 login.bmp');
  assert.strictEqual(d.hash(), '#b/cuj%2001%20login.bmp',
    'a colliding path that needs encoding must be written whole AND encoded');
}

// An uncollided file still gets the short form, which is the common case.
{
  const d = driver({images: COLLIDING});
  d.selectFile(COLLIDING[1].path);
  assert.strictEqual(d.hash(), '#cuj_02_dashboard.bmp');
}

// skipHash means what it says: the caller already has the hash it wants.
{
  const d = driver({images: COLLIDING, hash: '#cuj_01_login.bmp'});
  d.selectFile(COLLIDING[2].path, true);
  assert.strictEqual(d.hash(), '#cuj_01_login.bmp',
    'a selection with skipHash must leave the hash alone');
  assert.deepStrictEqual(d.loaded, [COLLIDING[2].path]);
}

// ── The round trip, which is #20 ────────────────────────────────────────────

// Click a file, let the write fire hashchange, read it back. This is the
// sequence the bug report describes, and the one the SPA's guard against
// re-selecting the current file depends on being the identity.
{
  const d = driver({
    images: COLLIDING, currentFile: COLLIDING[1].path, hash: '#cuj_02_dashboard.bmp'});
  d.selectFile(COLLIDING[2].path);
  assert.strictEqual(d.selectFileFromHash(), true);
  assert.strictEqual(d.state.currentFile, COLLIDING[2].path,
    'the selection must not bounce to the first file sharing the basename');
  assert.deepStrictEqual(d.selected, [],
    'a hash naming the current file must select nothing at all');
  assert.strictEqual(d.hash(), '#' + COLLIDING[2].path);
}

// The same, for the root-level collision the read order is the only thing that
// can settle: `name.png` *is* the ambiguous basename, so nothing can be
// written to disambiguate it. A read that scans basenames first lands on
// `dir/name.png` here while every assertion above still passes.
{
  const d = driver({
    images: ROOT_COLLIDING, currentFile: 'dir/name.png', hash: '#dir/name.png'});
  d.selectFile('name.png');
  assert.strictEqual(d.hash(), '#name.png');
  assert.strictEqual(d.selectFileFromHash(), true);
  assert.strictEqual(d.state.currentFile, 'name.png',
    'a root-level file must not resolve to the nested one sharing its name');
}

// ── Reading ─────────────────────────────────────────────────────────────────

// A link shared before this change keeps its meaning, and reading it does not
// rewrite it — nobody's pasted URL is silently upgraded under them.
{
  const d = driver({images: COLLIDING, hash: '#cuj_01_login.bmp'});
  assert.strictEqual(d.selectFileFromHash(), true);
  assert.deepStrictEqual(d.selected.map(s => s.path), [COLLIDING[0].path]);
  assert.ok(d.selected[0].skipHash, 'reading a hash must select with skipHash');
  assert.strictEqual(d.hash(), '#cuj_01_login.bmp', 'reading a hash must not rewrite it');
}

// A full path in the bar selects the file it names, which is the whole point
// of writing one.
{
  const d = driver({images: COLLIDING, hash: '#' + COLLIDING[2].path});
  assert.strictEqual(d.selectFileFromHash(), true);
  assert.deepStrictEqual(d.selected.map(s => s.path), [COLLIDING[2].path]);
}

// Percent-encoding survives the trip, which is what `#` and a space in a
// filename reach the read side as.
{
  const ODD_IMAGES = files(['a/é b.png', 'x/y/a#b.png']);
  const d = driver({images: ODD_IMAGES, hash: '#x/y/a%23b.png'});
  assert.strictEqual(d.selectFileFromHash(), true);
  assert.deepStrictEqual(d.selected.map(s => s.path), ['x/y/a#b.png']);
}

// Nothing to do: no hash, no match, no files.
for (const [label, opts] of [
  ['an empty hash', {images: COLLIDING, hash: ''}],
  ['a hash naming nothing', {images: COLLIDING, hash: '#absent.bmp'}],
  ['an empty file list', {images: [], hash: '#cuj_01_login.bmp'}],
]) {
  const d = driver(opts);
  assert.strictEqual(d.selectFileFromHash(), false, label + ' must resolve to nothing');
  assert.deepStrictEqual(d.selected, [], label + ' must select nothing');
  assert.strictEqual(d.state.currentFile, null);
}

console.log('test_hash_target: all checks passed');
