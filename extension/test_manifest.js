const assert = require('assert');
const fs = require('fs');
const path = require('path');

// Resolve the extension directory from Bazel runfiles
const runfiles = process.env.RUNFILES_DIR || '';
const extDir = path.join(runfiles, '_main', 'extension');

const manifest = JSON.parse(fs.readFileSync(path.join(extDir, 'manifest.json'), 'utf8'));

// Required Manifest V3 fields
assert.strictEqual(manifest.manifest_version, 3, 'manifest_version must be 3');
assert.ok(manifest.name, 'name is required');
assert.ok(manifest.version, 'version is required');
assert.ok(manifest.description, 'description is required');

// Content scripts
assert.ok(manifest.content_scripts, 'content_scripts is required');
assert.ok(manifest.content_scripts.length > 0, 'must have at least one content script entry');

const cs = manifest.content_scripts[0];
assert.ok(cs.matches.includes('https://github.com/*'), 'must match github.com');
assert.strictEqual(cs.run_at, 'document_idle', 'must run at document_idle');
assert.ok(cs.js.length >= 2, 'must have at least image_config.js and content.js');

// All referenced JS files must exist
for (const jsFile of cs.js) {
  const filePath = path.join(extDir, jsFile);
  assert.ok(fs.existsSync(filePath), 'content script file missing: ' + jsFile);
}

// Background service worker must exist
assert.ok(manifest.background, 'background is required');
assert.ok(manifest.background.service_worker, 'service_worker is required');
const swPath = path.join(extDir, manifest.background.service_worker);
assert.ok(fs.existsSync(swPath), 'service worker missing: ' + manifest.background.service_worker);

// Icon files must exist
assert.ok(manifest.icons, 'icons are required');
for (const [size, iconPath] of Object.entries(manifest.icons)) {
  const fullPath = path.join(extDir, iconPath);
  assert.ok(fs.existsSync(fullPath), 'icon file missing: ' + iconPath + ' (size ' + size + ')');
}

console.log('test_manifest: all checks passed');
