const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { loadExtensions } = require('./content_source');

const runfiles = process.env.RUNFILES_DIR || '';

// ── Test: image_config.js defines IMAGE_EXTENSIONS correctly ────────────────

const configPath = path.join(runfiles, '_main', 'extension', 'image_config.js');
const configSrc = fs.readFileSync(configPath, 'utf8');

// Execute the generated config to get IMAGE_EXTENSIONS
var IMAGE_EXTENSIONS;
eval(configSrc);
assert.ok(Array.isArray(IMAGE_EXTENSIONS), 'IMAGE_EXTENSIONS must be an array');
assert.ok(IMAGE_EXTENSIONS.length > 0, 'IMAGE_EXTENSIONS must not be empty');

// Every entry must start with a dot
for (const ext of IMAGE_EXTENSIONS) {
  assert.ok(ext.startsWith('.'), 'extension must start with dot: ' + ext);
}

// ── Test: image_config.js matches image_extensions.json ─────────────────────

const jsonPath = path.join(runfiles, '_main', 'image_extensions.json');
const extMime = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
const jsonKeys = Object.keys(extMime).sort();
const configKeys = IMAGE_EXTENSIONS.slice().sort();
assert.deepStrictEqual(configKeys, jsonKeys,
  'IMAGE_EXTENSIONS in config must match keys in image_extensions.json');

// Every value in JSON must be a valid MIME type
for (const [ext, mime] of Object.entries(extMime)) {
  assert.ok(mime.startsWith('image/'), ext + ' MIME must start with image/, got: ' + mime);
}

// ── Test: hasImageExtension logic ───────────────────────────────────────────
//
// The real function out of content.js, not a copy of it. This file used to
// hold its own, which would have kept passing after content.js started
// matching against a list fetched from the server.
//
// ensureExtensions is deliberately never called here, so the function matches
// against the bundled list — which is what the cases below are about. The
// fetch stub throws to make reaching the network a failure rather than a
// silent read; test_extensions_fetch.js is where the fetched list is
// exercised.

const { hasImageExtension } = loadExtensions({
  IMAGE_EXTENSIONS: IMAGE_EXTENSIONS,
  VR_BASE_URL: 'https://vr.invalid',
  fetch: () => { throw new Error('test_content_logic must not reach the network'); },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  Date: Date,
  console: console,
  AbortSignal: AbortSignal,
});

// Positive cases
assert.ok(hasImageExtension('screenshots/test.png'), '.png should match');
assert.ok(hasImageExtension('screenshots/test.PNG'), '.PNG should match (case-insensitive)');
assert.ok(hasImageExtension('photos/hero.jpg'), '.jpg should match');
assert.ok(hasImageExtension('photos/banner.jpeg'), '.jpeg should match');
assert.ok(hasImageExtension('photos/HERO.JPG'), '.JPG should match');
assert.ok(hasImageExtension('baseline/page.bmp'), '.bmp should match');
assert.ok(hasImageExtension('baseline/page.BMP'), '.BMP should match');
assert.ok(hasImageExtension('deep/nested/path/file.png'), 'nested paths should match');

// Negative cases
assert.ok(!hasImageExtension('src/main.py'), '.py should not match');
assert.ok(!hasImageExtension('docs/readme.txt'), '.txt should not match');
assert.ok(!hasImageExtension('data.json'), '.json should not match');
assert.ok(!hasImageExtension('noext'), 'no extension should not match');
assert.ok(!hasImageExtension('fake.pngx'), '.pngx should not match');
assert.ok(!hasImageExtension('image.svg'), '.svg should not match');

// ── Test: getPageContext logic ──────────────────────────────────────────────

function getPageContext(pathname) {
  var listMatch = pathname.match(/^\/([^\/]+)\/([^\/]+)\/pulls\/?$/);
  if (listMatch) {
    return { type: 'list', owner: listMatch[1], repo: listMatch[2] };
  }
  var detailMatch = pathname.match(/^\/([^\/]+)\/([^\/]+)\/pull\/(\d+)\/?/);
  if (detailMatch) {
    return {
      type: 'detail',
      owner: detailMatch[1],
      repo: detailMatch[2],
      prNumber: parseInt(detailMatch[3])
    };
  }
  return null;
}

// PR list pages
var ctx = getPageContext('/widdowson/apwphotos-appv2/pulls');
assert.deepStrictEqual(ctx, { type: 'list', owner: 'widdowson', repo: 'apwphotos-appv2' });

ctx = getPageContext('/widdowson/apwphotos-appv2/pulls/');
assert.deepStrictEqual(ctx, { type: 'list', owner: 'widdowson', repo: 'apwphotos-appv2' });

// PR detail pages
ctx = getPageContext('/widdowson/apwphotos-appv2/pull/378');
assert.deepStrictEqual(ctx, { type: 'detail', owner: 'widdowson', repo: 'apwphotos-appv2', prNumber: 378 });

ctx = getPageContext('/widdowson/apwphotos-appv2/pull/378/files');
assert.deepStrictEqual(ctx, { type: 'detail', owner: 'widdowson', repo: 'apwphotos-appv2', prNumber: 378 });

// Non-matching pages
assert.strictEqual(getPageContext('/widdowson/apwphotos-appv2'), null);
assert.strictEqual(getPageContext('/widdowson/apwphotos-appv2/issues'), null);
assert.strictEqual(getPageContext('/'), null);

console.log('test_content_logic: all checks passed');
