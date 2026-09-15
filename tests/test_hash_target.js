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

// ── The wiring ──────────────────────────────────────────────────────────────
//
// Everything above tests the extracted region. The SPA reaches it from two
// functions that touch the DOM and so cannot be run here — and every
// assertion above would still pass with either of them left on its own
// basename loop, which is precisely the state this PR found the file in.

const fromHash = bodyOf('selectFileFromHash');
assert.ok(/resolveHashTarget\s*\(\s*decodeURIComponent\s*\(\s*hash\s*\)\s*,\s*state\.images\s*\)/
  .test(fromHash),
  'selectFileFromHash must resolve the decoded hash through resolveHashTarget');
assert.ok(/selectFile\s*\([^)]*,\s*true\s*\)/.test(fromHash),
  'selectFileFromHash must select with skipHash, or reading a hash rewrites it');

const select = bodyOf('selectFile');
assert.ok(
  /location\.hash\s*=\s*encodeHashTarget\s*\(\s*hashTargetFor\s*\(\s*path\s*,\s*state\.images\s*\)\s*\)/
    .test(select),
  'selectFile must write encodeHashTarget(hashTargetFor(path, state.images))');

// Neither may keep a basename of its own: a second, unshared notion of what
// names a file is how the two halves came to disagree.
for (const [name, body] of [['selectFileFromHash', fromHash], ['selectFile', select]]) {
  assert.ok(!/\.split\s*\(\s*'\/'\s*\)/.test(body),
    name + ' must take basenames from the hash-target region, not compute its own');
}

console.log('test_hash_target: all checks passed');
