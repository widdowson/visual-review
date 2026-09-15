// Exercises the extension's use of the server's /api/extensions endpoint.
//
// The logic is extracted from content.js and run here rather than copied —
// see content_source.js for why, and for how the region's globals are stubbed.
//
// The property every case below is really about: the extension must never end
// up matching nothing. Adopting the server's list is the feature; keeping the
// bundled list whenever that list cannot be trusted is what makes the feature
// safe to ship, because an extension matching nothing looks exactly like a run
// of PRs that happen to have no images.

const assert = require('assert');
const { loadExtensions, bodyOf, sourceWithoutComments } = require('./content_source');

const BUNDLED = ['.png', '.bmp'];
const BASE_URL = 'https://vr.test';
const DAY_MS = 24 * 60 * 60 * 1000;
const NOW = 1_700_000_000_000;
const CACHE_KEY = 'vr_image_extensions';

// ── Stubs ───────────────────────────────────────────────────────────────────

function fakeStore(initial) {
  const store = new Map(initial ? Object.entries(initial) : []);
  return {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => { store.set(k, String(v)); },
    removeItem: (k) => { store.delete(k); },
    read: (k) => (store.has(k) ? store.get(k) : null),
  };
}

// `respond` is called with no arguments and returns what the fetch should do:
// a Response-ish object to resolve with, or an Error to reject with.
function fakeFetch(respond) {
  const calls = [];
  const fn = (url, opts) => {
    calls.push({ url, opts });
    const outcome = respond(calls.length);
    if (outcome instanceof Error) return Promise.reject(outcome);
    return Promise.resolve(outcome);
  };
  fn.calls = calls;
  return fn;
}

function jsonResponse(body, ok = true, status = 200) {
  return { ok, status, json: () => Promise.resolve(body) };
}

function setup(opts = {}) {
  const warnings = [];
  const fetchStub = opts.fetch || fakeFetch(() => jsonResponse({ extensions: ['.png', '.webp'] }));
  const store = opts.store || fakeStore();
  const now = opts.now === undefined ? NOW : opts.now;
  const mod = loadExtensions({
    IMAGE_EXTENSIONS: opts.bundled || BUNDLED,
    VR_BASE_URL: BASE_URL,
    fetch: fetchStub,
    localStorage: store,
    Date: { now: () => now },
    console: { warn: (...args) => warnings.push(args) },
  });
  return { mod, fetch: fetchStub, store, warnings };
}

// A cache entry the region should accept, unless `ts` says otherwise.
function cacheEntry(extensions, ts = NOW) {
  return { [CACHE_KEY]: JSON.stringify({ extensions, ts }) };
}

// Every case is async; run them in sequence and let a rejection fail the
// process, so a broken assertion inside a promise cannot pass as a green run.
const cases = [];
function test(name, fn) { cases.push([name, fn]); }

// ── Before the server answers, the bundled list is in force ─────────────────

test('matches the bundled list synchronously, before any fetch resolves', () => {
  const { mod, fetch } = setup();
  // No await: this is the state a PR row would see if it read the list in the
  // same tick the content script loaded.
  assert.ok(mod.hasImageExtension('shot.png'), 'bundled .png must match immediately');
  assert.ok(!mod.hasImageExtension('shot.webp'), 'the server list has not arrived yet');
  assert.strictEqual(fetch.calls.length, 0, 'nothing is fetched until ensureExtensions runs');
});

// ── The happy path ──────────────────────────────────────────────────────────

test('adopts the server list, replacing the bundled one', async () => {
  const { mod, fetch } = setup();
  const list = await mod.ensureExtensions();

  assert.deepStrictEqual(list, ['.png', '.webp']);
  assert.ok(mod.hasImageExtension('shot.webp'),
    'a format only the server knows about must now match');
  assert.ok(!mod.hasImageExtension('shot.bmp'),
    'the server list replaces the bundled one rather than adding to it');

  assert.strictEqual(fetch.calls.length, 1);
  assert.strictEqual(fetch.calls[0].url, BASE_URL + '/api/extensions');
  assert.deepStrictEqual(fetch.calls[0].opts, { credentials: 'omit' },
    'the request must not carry github.com cookies to a third-party host');
});

test('lowercases what the server sends', async () => {
  const { mod } = setup({ fetch: fakeFetch(() => jsonResponse({ extensions: ['.WEBP'] })) });
  const list = await mod.ensureExtensions();

  assert.deepStrictEqual(list, ['.webp']);
  assert.ok(mod.hasImageExtension('shot.webp'), 'lowercase path must match');
  assert.ok(mod.hasImageExtension('SHOT.WEBP'), 'uppercase path must match');
});

test('caches the adopted list with the current timestamp', async () => {
  const { mod, store } = setup();
  await mod.ensureExtensions();

  const entry = JSON.parse(store.read(CACHE_KEY));
  assert.deepStrictEqual(entry.extensions, ['.png', '.webp']);
  assert.strictEqual(entry.ts, NOW);
});

// ── The cache ───────────────────────────────────────────────────────────────

test('a fresh cache entry is used and nothing is fetched', async () => {
  const { mod, fetch } = setup({ store: fakeStore(cacheEntry(['.tiff'], NOW - 1000)) });
  const list = await mod.ensureExtensions();

  assert.deepStrictEqual(list, ['.tiff']);
  assert.ok(mod.hasImageExtension('scan.tiff'));
  assert.strictEqual(fetch.calls.length, 0, 'a fresh cache entry must not hit the network');
});

test('an entry one millisecond inside the window is still fresh', async () => {
  const { mod, fetch } = setup({ store: fakeStore(cacheEntry(['.tiff'], NOW - DAY_MS + 1)) });
  assert.deepStrictEqual(await mod.ensureExtensions(), ['.tiff']);
  assert.strictEqual(fetch.calls.length, 0);
});

test('an entry one millisecond past the window is refetched', async () => {
  const { mod, fetch } = setup({ store: fakeStore(cacheEntry(['.tiff'], NOW - DAY_MS - 1)) });
  assert.deepStrictEqual(await mod.ensureExtensions(), ['.png', '.webp']);
  assert.strictEqual(fetch.calls.length, 1, 'a stale entry must be refetched');
});

test('an entry the region cannot trust is ignored and refetched', async () => {
  const unusable = {
    'not JSON at all': 'kaboom',
    'JSON null': 'null',
    'no ts': JSON.stringify({ extensions: ['.tiff'] }),
    'ts is a string': JSON.stringify({ extensions: ['.tiff'], ts: String(NOW) }),
    'no extensions': JSON.stringify({ ts: NOW }),
    'extensions is a string': JSON.stringify({ extensions: '.tiff', ts: NOW }),
    'extensions is empty': JSON.stringify({ extensions: [], ts: NOW }),
    'an entry has no dot': JSON.stringify({ extensions: ['tiff'], ts: NOW }),
  };

  for (const [label, raw] of Object.entries(unusable)) {
    const { mod, fetch } = setup({ store: fakeStore({ [CACHE_KEY]: raw }) });
    assert.deepStrictEqual(await mod.ensureExtensions(), ['.png', '.webp'],
      'cache entry (' + label + ') must be ignored in favour of the server');
    assert.strictEqual(fetch.calls.length, 1, 'cache entry (' + label + ') must be refetched');
  }
});

// ── Everything that must fall back to the bundled list ──────────────────────

test('an HTTP error keeps the bundled list and caches nothing', async () => {
  const { mod, fetch, store, warnings } = setup({
    fetch: fakeFetch(() => jsonResponse({ extensions: ['.webp'] }, false, 503)),
  });
  const list = await mod.ensureExtensions();

  assert.deepStrictEqual(list, BUNDLED);
  assert.ok(mod.hasImageExtension('shot.png'), 'the extension must still match images');
  assert.ok(!mod.hasImageExtension('shot.webp'), 'a 503 body must not be adopted');
  assert.strictEqual(store.read(CACHE_KEY), null, 'a failure must not poison the cache');
  assert.strictEqual(fetch.calls.length, 1);
  assert.strictEqual(warnings.length, 1, 'the fallback must say so in the console');
});

test('a network failure keeps the bundled list', async () => {
  const { mod, store } = setup({ fetch: fakeFetch(() => new Error('DNS is on fire')) });
  assert.deepStrictEqual(await mod.ensureExtensions(), BUNDLED);
  assert.strictEqual(store.read(CACHE_KEY), null);
});

test('a body that is not JSON keeps the bundled list', async () => {
  const { mod, store } = setup({
    fetch: fakeFetch(() => ({
      ok: true,
      status: 200,
      json: () => Promise.reject(new SyntaxError('Unexpected token <')),
    })),
  });
  assert.deepStrictEqual(await mod.ensureExtensions(), BUNDLED);
  assert.strictEqual(store.read(CACHE_KEY), null);
});

test('a malformed payload keeps the bundled list and caches nothing', async () => {
  const malformed = {
    'empty object': {},
    'null body': null,
    'extensions is null': { extensions: null },
    'extensions is empty': { extensions: [] },
    'extensions is a string': { extensions: '.webp' },
    'extensions is an object': { extensions: { '.webp': 'image/webp' } },
    'a bare array, not an object': ['.webp'],
    'an entry is a number': { extensions: ['.png', 5] },
    'an entry has no dot': { extensions: ['webp'] },
    'an entry is a bare dot': { extensions: ['.'] },
    'an entry is empty': { extensions: [''] },
  };

  for (const [label, body] of Object.entries(malformed)) {
    const { mod, store } = setup({ fetch: fakeFetch(() => jsonResponse(body)) });
    assert.deepStrictEqual(await mod.ensureExtensions(), BUNDLED,
      'payload (' + label + ') must be rejected');
    assert.ok(mod.hasImageExtension('shot.png'),
      'payload (' + label + ') must leave the extension matching images');
    assert.strictEqual(store.read(CACHE_KEY), null,
      'payload (' + label + ') must not be cached');
  }
});

// ── One fetch per page ──────────────────────────────────────────────────────

test('overlapping callers share one request', async () => {
  const { mod, fetch } = setup();
  const [a, b] = await Promise.all([mod.ensureExtensions(), mod.ensureExtensions()]);
  await mod.ensureExtensions();

  assert.deepStrictEqual(a, ['.png', '.webp']);
  assert.deepStrictEqual(b, ['.png', '.webp']);
  assert.strictEqual(fetch.calls.length, 1,
    'a PR list page asks once per row and must still make one request');
});

test('a failure is not retried per caller', async () => {
  const { mod, fetch } = setup({ fetch: fakeFetch(() => new Error('unreachable')) });
  await mod.ensureExtensions();
  await mod.ensureExtensions();
  assert.strictEqual(fetch.calls.length, 1,
    'an unreachable server must cost one request, not one per PR row');
});

test('a localStorage that throws does not break the fetch', async () => {
  // Chrome throws here on a quota error and when site data is blocked. The
  // read and the write are both wrapped, and this drives both: the read throws
  // on the way in, the write on the way out, and the server list still lands.
  const hostile = {
    getItem: () => { throw new Error('site data is blocked'); },
    setItem: () => { throw new Error('quota exceeded'); },
    removeItem: () => {},
    read: () => null,
  };
  const { mod, fetch } = setup({ store: hostile });

  assert.deepStrictEqual(await mod.ensureExtensions(), ['.png', '.webp']);
  assert.ok(mod.hasImageExtension('shot.webp'));
  assert.strictEqual(fetch.calls.length, 1);
});

// ── How the region is wired into the rest of content.js ──────────────
//
// Everything above runs the region in isolation, so all of it still passes
// with the region wired to nothing. prHasImageFiles is where it is wired in,
// and it touches the DOM and the network, so these are structural checks on
// its source rather than runs of it. Comments are stripped first, so a comment
// claiming the call is made does not satisfy them.

test('prHasImageFiles awaits the list before matching against it', () => {
  const body = bodyOf('prHasImageFiles');
  const awaited = body.indexOf('await ensureExtensions()');
  const matched = body.indexOf('hasImageExtension(');

  assert.ok(awaited > 0,
    'prHasImageFiles must await ensureExtensions(); without it the fetched ' +
    'list is never asked for and the endpoint might as well not exist');
  assert.ok(matched > 0, 'prHasImageFiles must be where paths are matched');
  assert.ok(awaited < matched,
    'the await must come before the match, or the first page load decides ' +
    'against the bundled list however current the server is');
});

test('prHasImageFiles is the only place the list is matched against', () => {
  // The one await above covers the list page, the detail page and the
  // hovercard only because all three reach the match through this function.
  // A second call site elsewhere needs its own await — and should change this
  // count deliberately rather than inherit a guarantee that no longer holds.
  const uses = sourceWithoutComments.split('hasImageExtension(').length - 1;
  assert.strictEqual(uses, 2,
    'expected exactly the definition and one call site, found ' + uses);
});

// ── normalizeExtensions, directly ───────────────────────────────────────────

test('normalizeExtensions accepts a well-formed list and rejects the rest', () => {
  const { mod } = setup();
  assert.deepStrictEqual(mod.normalizeExtensions(['.PNG', '.webp']), ['.png', '.webp']);
  assert.deepStrictEqual(mod.normalizeExtensions(['.a']), ['.a'],
    'a two-character extension is the shortest legal one');

  for (const bad of [null, undefined, '.png', 0, [], ['.'], [''], ['png'], ['.png', null]]) {
    assert.strictEqual(mod.normalizeExtensions(bad), null,
      'must reject ' + JSON.stringify(bad));
  }
});

// ── Runner ──────────────────────────────────────────────────────────────────

(async () => {
  for (const [name, fn] of cases) {
    await fn();
    console.log('  ✓ ' + name);
  }
  console.log('test_extensions_fetch: all ' + cases.length + ' cases passed');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
