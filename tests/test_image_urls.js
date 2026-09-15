// Exercises buildImageUrls, which lives in static/index.html. Both the real
// load and the speculative prefetch go through it; before they did, the
// prefetch built the base URL from a renamed file's *new* path, so every
// renamed file prefetched a 404 and then had to be fetched again on arrival.

const assert = require('assert');
const { extract, bodyOf } = require('./spa_source');

const buildImageUrls = extract('image-urls', 'buildImageUrls');

const API = '/api/o/r/pr/7';
const BASE = 'b'.repeat(40);
const HEAD = 'h'.repeat(40);
const urls = f => buildImageUrls(f, API, BASE, HEAD);

// ── The four file statuses ──────────────────────────────────────────────────

assert.deepStrictEqual(urls({path: 'a/b.png', status: 'modified'}), {
  base: API + '/image?path=a%2Fb.png&ref=' + BASE,
  head: API + '/image?path=a%2Fb.png&ref=' + HEAD,
});

// An added file has no base side, a removed one no head side. Asking for
// either would be a guaranteed 404.
assert.strictEqual(urls({path: 'a/b.png', status: 'added'}).base, null);
assert.strictEqual(urls({path: 'a/b.png', status: 'added'}).head,
  API + '/image?path=a%2Fb.png&ref=' + HEAD);
assert.strictEqual(urls({path: 'a/b.png', status: 'removed'}).head, null);
assert.strictEqual(urls({path: 'a/b.png', status: 'removed'}).base,
  API + '/image?path=a%2Fb.png&ref=' + BASE);

// The regression this file exists for: the base side of a renamed file is at
// its previous path, and only the base side.
const renamed = urls({path: 'new/name.png', status: 'renamed', previous_filename: 'old/name.png'});
assert.strictEqual(renamed.base, API + '/image?path=old%2Fname.png&ref=' + BASE,
  'a renamed file must take its base image from previous_filename');
assert.strictEqual(renamed.head, API + '/image?path=new%2Fname.png&ref=' + HEAD,
  'the head side stays at the new path');

// GitHub can report `renamed` without a previous_filename; fall back rather
// than building a URL containing "undefined".
const noPrev = urls({path: 'new/name.png', status: 'renamed'});
assert.strictEqual(noPrev.base, API + '/image?path=new%2Fname.png&ref=' + BASE);
for (const u of [noPrev.base, noPrev.head]) {
  assert.ok(!/undefined|null/.test(u), 'no placeholder leaked into ' + u);
}

// previous_filename on a status that is not a rename is ignored.
assert.strictEqual(
  urls({path: 'a.png', status: 'modified', previous_filename: 'b.png'}).base,
  API + '/image?path=a.png&ref=' + BASE);

// ── Encoding ────────────────────────────────────────────────────────────────

// Paths reach the proxy as a query parameter, so every separator and space
// has to survive the trip.
const odd = urls({path: 'dir with space/a&b=c?d#e/é.png', status: 'modified'});
assert.ok(odd.head.endsWith('&ref=' + HEAD));
const parsed = new URLSearchParams(odd.head.slice(odd.head.indexOf('?') + 1));
assert.strictEqual(parsed.get('path'), 'dir with space/a&b=c?d#e/é.png',
  'the path must round-trip through the query string');
assert.strictEqual(parsed.get('ref'), HEAD);

// ── Degenerate input ────────────────────────────────────────────────────────

assert.deepStrictEqual(buildImageUrls(null, API, BASE, HEAD), {base: null, head: null});
assert.deepStrictEqual(buildImageUrls(undefined, API, BASE, HEAD), {base: null, head: null});
assert.deepStrictEqual(buildImageUrls({status: 'modified'}, API, BASE, HEAD),
  {base: null, head: null}, 'a file with no path has no URLs');

// An unknown status is treated as having both sides, which is what the SPA's
// own getFileStatus fallback assumes.
const unknown = urls({path: 'a.png', status: 'copied'});
assert.ok(unknown.base && unknown.head);

// ── The adapter ─────────────────────────────────────────────────────────────
// Everything above tests the pure region. The SPA reaches it through a
// one-line adapter that supplies apiBase and the two refs, and that line is
// outside the region — swap its last two arguments and every assertion above
// still passes while the page shows every file's base and current the wrong
// way round. Structural, like the wiring checks in test_prefetch_policy.js.

const adapter = bodyOf('imageUrls');
assert.ok(/return\s+buildImageUrls\s*\(/.test(adapter),
  'imageUrls must delegate to the extracted buildImageUrls');
assert.ok(
  /buildImageUrls\s*\(\s*fileData\s*,\s*apiBase\s*,\s*state\.baseRef\s*,\s*state\.headRef\s*\)/
    .test(adapter),
  'imageUrls must pass the refs in base, head order');

console.log('test_image_urls: all checks passed');
