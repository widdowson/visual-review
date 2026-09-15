// Exercises the rename display decision, which lives in static/index.html.
//
// Issue #14 asks for a renamed file to be shown once instead of through four
// comparison modes, "since there is nothing to compare". The trap is that
// GitHub's `renamed` status does not mean that: git's rename detection matches
// a file that was moved *and* edited (R096 and friends), GitHub reports those
// as `renamed` too, and for a binary file it sends no patch and zero additions
// and deletions either way. So the status alone cannot answer the question and
// the pixels have to.

const assert = require('assert');
const { extract, bodyOf } = require('./spa_source');

const renameDisplay = extract('rename-display', 'renameDisplay');

// ── The decision ────────────────────────────────────────────────────────────

const same = [false, false, false];
const differs = [false, true, false];

// Only a rename is this function's business; everything else falls through to
// the caller's ordinary mode dispatch.
for (const status of ['modified', 'added', 'removed', 'copied', 'changed']) {
  assert.strictEqual(renameDisplay(status, same), null,
    status + ' is not a rename, whatever its pixels say');
  assert.strictEqual(renameDisplay(status, differs), null);
}

// A move that changed nothing: one image, no modes.
assert.strictEqual(renameDisplay('renamed', same), 'single');

// The regression this file exists for. A move that also changed the image has
// everything to compare, and collapsing it to a single image is the only view
// in which the change could never be seen.
assert.strictEqual(renameDisplay('renamed', differs), 'compare',
  'a rename whose pixels moved must keep the comparison modes');
assert.strictEqual(renameDisplay('renamed', [true]), 'compare');
assert.strictEqual(renameDisplay('renamed', [true, true]), 'compare');

// ── Not being able to tell is not the same as being identical ───────────────
//
// computeRowDiffMap returns null when a side is missing or zero-sized. Reading
// that as "identical" would hide a file whose base image failed to load behind
// a view that claims there is nothing to see.

for (const unknown of [null, undefined, [], 0, '', false]) {
  assert.strictEqual(renameDisplay('renamed', unknown), 'compare',
    'an unusable row map must not be read as proof the sides match: ' +
    JSON.stringify(unknown));
}

// Truthy rows are what count, so the map's own representation of "this row is
// clean" must be falsy end to end.
assert.strictEqual(renameDisplay('renamed', new Array(3).fill(false)), 'single');

// ── computeRowDiffMap at an exact threshold ─────────────────────────────────
//
// The decision above is only as good as the map it is handed, and the map is
// built by a function that touches a canvas and so cannot be extracted. Run it
// against a fake one instead: this is the assertion that `threshold || 10`
// would fail, by turning the exact comparison the rename check asks for back
// into the gutter's tolerance.

// A 1x2 image: one row of `rows[y]` repeated across the width, as RGBA.
function image(id, rows) {
  return { id: id, naturalWidth: 1, naturalHeight: rows.length, rows: rows };
}

function fakeDocument() {
  return {
    createElement() {
      const canvas = { width: 0, height: 0, drawn: null };
      canvas.getContext = () => ({
        drawImage(img) { canvas.drawn = img; },
        getImageData(x, y, w, h) {
          const data = new Uint8ClampedArray(w * h * 4);
          const rows = (canvas.drawn && canvas.drawn.rows) || [];
          for (let row = 0; row < h; row++) {
            const value = row < rows.length ? rows[row] : 0;
            for (let col = 0; col < w; col++) {
              const i = (row * w + col) * 4;
              data[i] = data[i + 1] = data[i + 2] = value;
              data[i + 3] = 255;
            }
          }
          return { data: data };
        },
      });
      return canvas;
    },
  };
}

const computeRowDiffMap = new Function(
  'document', 'return (' + bodyOf('computeRowDiffMap') + ');')(fakeDocument());

// Row 1 differs by 5 per channel — under the gutter's tolerance of 10, over an
// exact comparison.
const base = image('base', [0, 100]);
const head = image('head', [0, 105]);

assert.deepStrictEqual(computeRowDiffMap(base, head, 10), [false, false],
  'the gutter tolerance still ignores a 5-per-channel difference');
assert.deepStrictEqual(computeRowDiffMap(base, head, 0), [false, true],
  'an explicit threshold of 0 must compare exactly, not fall back to 10');

// Which is the whole point: the two thresholds give the rename check opposite
// answers on this pair, and only the exact one is right.
assert.strictEqual(renameDisplay('renamed', computeRowDiffMap(base, head, 10)), 'single');
assert.strictEqual(renameDisplay('renamed', computeRowDiffMap(base, head, 0)), 'compare');

// The default is unchanged for every caller that does not pass one.
assert.deepStrictEqual(computeRowDiffMap(base, head, undefined), [false, false]);
assert.deepStrictEqual(computeRowDiffMap(base, image('head', [0, 200]), 0), [false, true]);

// A missing side has no map at all, which is the input the "cannot tell" cases
// above are about — so the two halves really do meet.
assert.strictEqual(computeRowDiffMap(null, head, 0), null);
assert.strictEqual(computeRowDiffMap(base, null, 0), null);

// ── The wiring ──────────────────────────────────────────────────────────────
//
// Everything above tests decisions. renderComparison is what asks them, and it
// touches the DOM, so it is checked structurally — the same shape as the
// adapter check in test_image_urls.js. Swap the 0 for a 10 here, or hand it the
// gutter's rowMap, and every assertion above still passes while the page hides
// a real change behind a single image.

const render = bodyOf('renderComparison');

assert.ok(
  /renameDisplay\s*\(\s*fileStatus\s*,\s*computeRowDiffMap\s*\(\s*state\.baseImg\s*,\s*state\.headImg\s*,\s*0\s*\)\s*\)/
    .test(render),
  'renderComparison must decide the rename view from an exact row map');

assert.ok(
  /computeRowDiffMap\s*\(\s*state\.baseImg\s*,\s*state\.headImg\s*,\s*10\s*\)/.test(render),
  'the diff gutter must still get its own map at the gutter tolerance');

// The single-image view is reachable only through that decision. A bare
// `fileStatus === 'renamed'` branch is what this PR removed.
assert.strictEqual(render.split('renderRenamed(').length - 1, 1,
  'renderComparison must call renderRenamed in exactly one place');
assert.ok(
  /if\s*\(\s*renameMode === 'single'\s*\)\s*\{\s*renderRenamed\(\s*\);/.test(render),
  'renderRenamed must be guarded by the single-image verdict');
assert.ok(
  !/fileStatus\s*===\s*'renamed'\s*\)\s*\{\s*renderRenamed/.test(render),
  'the status alone must not select the single-image view');

// ── The shared label ────────────────────────────────────────────────────────
//
// Both rename views say where the file came from, so the label is one function.
// It needs a DOM, so it is run against a stub rather than extracted.

const renameLabel = new Function(
  'document', 'state', 'getFileData', 'escHtml',
  'return (' + bodyOf('renameLabel') + ');');

function labelFor(fileData, currentFile) {
  const doc = {
    createElement() {
      return { className: '', innerHTML: '' };
    },
  };
  return renameLabel(
    doc, { currentFile: currentFile }, () => fileData, s => String(s))();
}

const label = labelFor({ previous_filename: 'old/a.bmp' }, 'new/a.bmp');
assert.strictEqual(label.className, 'renamed-label');
assert.ok(label.innerHTML.includes('old/a.bmp'), 'the label names the old path');
assert.ok(label.innerHTML.includes('new/a.bmp'), 'the label names the new path');
assert.ok(label.innerHTML.indexOf('old/a.bmp') < label.innerHTML.indexOf('new/a.bmp'),
  'old path first, new path second — the arrow points one way');

// GitHub can report a rename without a previous path (see buildImageUrls, which
// has the same fallback). Nothing to say then, and nothing rendered.
assert.strictEqual(labelFor({}, 'new/a.bmp'), null);
assert.strictEqual(labelFor(null, 'new/a.bmp'), null);

// The label is escaped, not interpolated raw.
const escaped = new Function(
  'document', 'state', 'getFileData', 'escHtml',
  'return (' + bodyOf('renameLabel') + ');')(
  { createElement: () => ({ className: '', innerHTML: '' }) },
  { currentFile: 'new.bmp' },
  () => ({ previous_filename: '<img src=x onerror=1>' }),
  s => String(s).replace(/</g, '&lt;'))();
assert.ok(!escaped.innerHTML.includes('<img'), 'the old path goes through escHtml');

// Both views use it, so neither can drift into building its own.
assert.ok(/renameLabel\(\s*\)/.test(bodyOf('renderRenamed')),
  'the single-image view must use the shared label');
assert.ok(/renameLabel\(\s*\)/.test(render),
  'the comparison view must use the shared label');

// ── What an unfetchable base side says ──────────────────────────────────────
//
// Keeping the modes on a rename we cannot prove identical means side-by-side
// now renders an empty base panel for a file that is not an added one. The
// panel used to say "(new file)" unconditionally, which for a rename is a claim
// about the diff rather than about the fetch that failed.

const sideBySide = bodyOf('renderSideBySide');
assert.ok(
  /getFileStatus\(state\.currentFile\)\s*===\s*'added'/.test(sideBySide),
  'the empty base panel must name a new file only when the file is added');
assert.ok(
  !/=\s*'No base image \(new file\)'\s*;/.test(sideBySide),
  'the "new file" wording must not be unconditional');

console.log('test_rename_display: all checks passed');
