const assert = require('assert');
const fs = require('fs');
const path = require('path');

const runfiles = process.env.RUNFILES_DIR || '';
const extDir = path.join(runfiles, '_main', 'extension');

const manifest = JSON.parse(fs.readFileSync(path.join(extDir, 'manifest.json'), 'utf8'));

// PNG magic bytes: 89 50 4E 47 0D 0A 1A 0A
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);

for (const [size, iconPath] of Object.entries(manifest.icons)) {
  const fullPath = path.join(extDir, iconPath);
  const data = fs.readFileSync(fullPath);

  // Verify PNG signature
  assert.ok(data.length >= 8, iconPath + ' is too small to be a valid PNG');
  const header = data.subarray(0, 8);
  assert.ok(header.equals(PNG_MAGIC), iconPath + ' does not have valid PNG magic bytes');

  // Verify IHDR chunk declares the expected dimensions
  // IHDR is always the first chunk: bytes 16-19 = width, 20-23 = height (big-endian)
  if (data.length >= 24) {
    const width = data.readUInt32BE(16);
    const height = data.readUInt32BE(20);
    const expectedSize = parseInt(size);
    assert.strictEqual(width, expectedSize, iconPath + ' width should be ' + expectedSize + ' but got ' + width);
    assert.strictEqual(height, expectedSize, iconPath + ' height should be ' + expectedSize + ' but got ' + height);
  }

  console.log(iconPath + ': valid ' + size + 'x' + size + ' PNG (' + data.length + ' bytes)');
}

console.log('test_icons: all checks passed');
