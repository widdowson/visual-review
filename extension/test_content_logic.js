const assert = require('assert');
const fs = require('fs');
const path = require('path');

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

function hasImageExtension(filePath) {
  var lower = filePath.toLowerCase();
  for (var i = 0; i < IMAGE_EXTENSIONS.length; i++) {
    if (lower.endsWith(IMAGE_EXTENSIONS[i])) return true;
  }
  return false;
}

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
