// Exercises the rename display decision and the dispatch that acts on it, both
// of which live in static/index.html.
//
// Issue #14 asks for a renamed file to be shown once instead of through four
// comparison modes, "since there is nothing to compare". The trap is that
// GitHub's `renamed` status does not mean that: git's rename detection matches
// a file that was moved *and* edited (R096 and friends), GitHub reports those
// as `renamed` too, and for a binary file it sends no patch and zero additions
// and deletions either way. So the status alone cannot answer the question and
// the pixels have to.
//
// The file is in three parts. The decision is a pure region, extracted and run.
// computeRowDiffMap and the two render functions touch a DOM, so they are run
// against stubs — not checked by pattern, because a regex that matches the text
// of a call passes whether or not the call does anything.

const assert = require('assert');
const { extract, bodyOf } = require('./spa_source');

const renameDisplay = extract('rename-display', 'renameDisplay');

// Two same-size images. The decision needs their dimensions, nothing else.
const A = { naturalWidth: 100, naturalHeight: 50, src: 'a' };
const B = { naturalWidth: 100, naturalHeight: 50, src: 'b' };
const verdict = (status, rowMap, base, head) =>
  renameDisplay(status, base === undefined ? A : base, head === undefined ? B : head, rowMap);

const same = [false, false, false];
const differs = [false, true, false];

// ── The decision ────────────────────────────────────────────────────────────

// Only a rename is this function's business; everything else falls through to
// the caller's ordinary mode dispatch. `unchanged` and `copied` are in the list
// because GitHub emits them too.
for (const status of ['modified', 'added', 'removed', 'copied', 'changed', 'unchanged']) {
  assert.strictEqual(verdict(status, same), null,
    status + ' is not a rename, whatever its pixels say');
  assert.strictEqual(verdict(status, differs), null);
}

// A move that changed nothing: one image, no modes.
assert.strictEqual(verdict('renamed', same), 'single');

// The regression this file exists for. A move that also changed the image has
// everything to compare, and collapsing it to a single image is the only view
// in which the change could never be seen.
assert.strictEqual(verdict('renamed', differs), 'compare',
  'a rename whose pixels moved must keep the comparison modes');
assert.strictEqual(verdict('renamed', [true]), 'compare');
assert.strictEqual(verdict('renamed', [true, true]), 'compare');

// ── Not being able to tell is not the same as being identical ───────────────
//
// computeRowDiffMap returns null when a side is missing or zero-sized. Reading
// that as "identical" would hide a file whose base image failed to load behind
// a view that claims there is nothing to see.

for (const unknown of [null, undefined, [], 0, '', false]) {
  assert.strictEqual(verdict('renamed', unknown), 'compare',
    'an unusable row map must not be read as proof the sides match: ' +
    JSON.stringify(unknown));
}

// ── Dimensions are checked before any pixel is ──────────────────────────────
//
// A row map on its own cannot prove two images identical, which is what the
// first version of this change got wrong. computeRowDiffMap draws both sides
// onto a canvas sized to the larger of the two in each dimension, so the region
// a smaller image does not cover reads back as *transparent black* — the same
// three channel values as opaque black. Measured in Chromium against the real
// functions: a 200x100 all-black image against a 200x160 all-black one came
// back `single`, and so did 300x100 against 200x100.

const tall = { naturalWidth: 100, naturalHeight: 80, src: 'c' };
const wide = { naturalWidth: 160, naturalHeight: 50, src: 'd' };

assert.strictEqual(verdict('renamed', same, A, tall), 'compare',
  'a taller head image is not the same image, whatever the row map says');
assert.strictEqual(verdict('renamed', same, A, wide), 'compare',
  'a wider head image is not the same image, whatever the row map says');
assert.strictEqual(verdict('renamed', same, tall, A), 'compare',
  'and the same in the other direction');

// A missing side is not a match either, even with an all-clean map handed in.
assert.strictEqual(verdict('renamed', same, null, B), 'compare');
assert.strictEqual(verdict('renamed', same, A, null), 'compare');

// Nor are two zero-sized images: nothing was compared, so nothing was proven.
const empty = { naturalWidth: 0, naturalHeight: 0, src: 'e' };
assert.strictEqual(verdict('renamed', same, empty, empty), 'compare');
assert.strictEqual(verdict('renamed', same,
  { naturalWidth: 100, naturalHeight: 0 }, { naturalWidth: 100, naturalHeight: 0 }), 'compare');

// ── computeRowDiffMap ───────────────────────────────────────────────────────
//
// The decision is only as good as the map it is handed, and the map is built by
// a function that touches a canvas and so cannot be extracted. Run it against a
// fake one instead.

// Rows are given as full RGBA, one row per entry, so a bug in one channel's
// comparison cannot be masked by a neighbouring channel seeing the same change.
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
            const rgba = row < rows.length ? rows[row] : [0, 0, 0, 0];
            for (let col = 0; col < w; col++) {
              const i = (row * w + col) * 4;
              for (let ch = 0; ch < 4; ch++) data[i + ch] = rgba[ch];
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

const BLACK = [0, 0, 0, 255];

// Each channel on its own, so no channel's assertion can ride on another's.
for (const [label, changed] of [
  ['red', [5, 0, 0, 255]],
  ['green', [0, 5, 0, 255]],
  ['blue', [0, 0, 5, 255]],
]) {
  const base = image('base', [BLACK, BLACK]);
  const head = image('head', [BLACK, changed]);
  assert.deepStrictEqual(computeRowDiffMap(base, head, 10), [false, false],
    'the gutter tolerance still ignores a 5-per-channel ' + label + ' difference');
  assert.deepStrictEqual(computeRowDiffMap(base, head, 0), [false, true],
    'an explicit threshold of 0 must compare ' + label + ' exactly, not fall back to 10');
}

// Which is the whole point: the two thresholds give the rename check opposite
// answers on the same pair, and only the exact one is right.
const exactBase = image('base', [BLACK, BLACK]);
const exactHead = image('head', [BLACK, [5, 0, 0, 255]]);
assert.strictEqual(verdict('renamed', computeRowDiffMap(exactBase, exactHead, 10)), 'single');
assert.strictEqual(verdict('renamed', computeRowDiffMap(exactBase, exactHead, 0)), 'compare');

// Alpha is compared only when asked for. The gutter has always compared the
// three colour channels and still does; the rename check needs the fourth,
// because a fully transparent image against a solid one of the same size read
// as identical on the colour channels alone (measured in Chromium).
const opaque = image('base', [BLACK, BLACK]);
const transparent = image('head', [BLACK, [0, 0, 0, 0]]);
assert.deepStrictEqual(computeRowDiffMap(opaque, transparent, 0), [false, false],
  'the gutter must not start flagging alpha-only differences');
assert.deepStrictEqual(computeRowDiffMap(opaque, transparent, 0, true), [false, true],
  'the rename check must see an alpha-only difference');
assert.strictEqual(verdict('renamed', computeRowDiffMap(opaque, transparent, 0)), 'single');
assert.strictEqual(verdict('renamed', computeRowDiffMap(opaque, transparent, 0, true)), 'compare');

// The default is unchanged for every caller that does not pass a threshold.
assert.deepStrictEqual(computeRowDiffMap(exactBase, exactHead, undefined), [false, false]);

// A missing side has no map at all, which is the input the "cannot tell" cases
// above are about — so the two halves really do meet.
assert.strictEqual(computeRowDiffMap(exactBase, null, 0), null);
assert.strictEqual(computeRowDiffMap(null, exactHead, 0), null);

// ── The dispatch ────────────────────────────────────────────────────────────
//
// renderComparison is what asks the decision and acts on it, and acting on it
// is most of what a reader sees. It touches the DOM, so it is run here against
// a stub one: the real renderComparison, the real renderDisplay, the real
// renameLabel and the real renderRenamed over stub elements, with only the
// canvas work and the four mode renderers replaced. Checking this by pattern
// instead would pass on an implementation that computes the label and drops it.

function stubEl() {
  const el = {
    children: [], className: '', innerHTML: '', textContent: '', style: {},
    classes: new Set(),
    appendChild(child) { el.children.push(child); return child; },
    querySelectorAll() { return []; },
  };
  el.classList = {
    add(c) { el.classes.add(c); },
    remove(c) { el.classes.delete(c); },
    contains(c) { return el.classes.has(c); },
  };
  return el;
}

// rowMaps records every computeRowDiffMap call renderComparison makes, which is
// how the exact threshold, the alpha flag and the pass count are all checked
// without coupling the test to the shape of the expression that makes them.
function render(file, baseImg, headImg, rowMap) {
  const doc = { createElement: () => stubEl(), getElementById: () => null };
  const viewport = stubEl(), imageInfo = stubEl(), modeToolbar = stubEl();
  const calls = { rowMaps: [], modes: [], renamed: 0 };

  const state = {
    currentFile: file.path, images: [file],
    baseImg: baseImg, headImg: headImg,
    mode: 'side-by-side', loadError: null, diffClusters: null,
  };

  const getFileData = p => state.images.find(i => i.path === p) || null;
  const getFileStatus = p => (getFileData(p) || { status: 'modified' }).status;
  const esc = s => String(s);

  const renameLabel = new Function(
    'document', 'state', 'getFileData', 'escHtml',
    'return (' + bodyOf('renameLabel') + ');')(doc, state, getFileData, esc);

  const renderRenamed = new Function(
    'modeToolbar', 'document', 'state', 'renameLabel', 'viewport',
    'return (' + bodyOf('renderRenamed') + ');')(
    modeToolbar, doc, state, renameLabel, viewport);

  const renderComparison = new Function(
    'viewport', 'imageInfo', 'state', 'hideLoupes', 'gutterRefreshCallbacks',
    'getFileStatus', 'computeRowDiffMap', 'computeDiffClusters', 'renameDisplay',
    'renderRenamed', 'renameLabel', 'escHtml', 'escAttr', 'sha256Hex',
    'owner', 'repo', 'prNumber', 'document',
    'renderSideBySide', 'renderCrossfade', 'renderSwipe', 'renderDiffOverlay',
    'return (' + bodyOf('renderComparison') + ');')(
    viewport, imageInfo, state, () => {}, [],
    getFileStatus,
    (b, h, threshold, includeAlpha) => {
      calls.rowMaps.push({ threshold: threshold, includeAlpha: !!includeAlpha });
      return rowMap;
    },
    () => [],
    renameDisplay,
    () => { calls.renamed++; renderRenamed(); },
    renameLabel, esc, esc,
    () => ({ then() {} }),
    'o', 'r', 1, doc,
    () => calls.modes.push('side-by-side'),
    () => calls.modes.push('crossfade'),
    () => calls.modes.push('swipe'),
    () => calls.modes.push('diff'));

  renderComparison();
  return { viewport: viewport, modeToolbar: modeToolbar, calls: calls };
}

const RENAMED = { path: 'new/a.bmp', status: 'renamed', previous_filename: 'old/a.bmp' };
const MODIFIED = { path: 'plain.bmp', status: 'modified' };

// -- A pure rename renders once, with the label, and no modes ----------------

const pure = render(RENAMED, A, B, same);
assert.strictEqual(pure.calls.renamed, 1, 'a pure rename renders the single-image view');
assert.deepStrictEqual(pure.calls.modes, [], 'and no comparison mode at all');
assert.strictEqual(pure.modeToolbar.style.display, 'none', 'with the mode toolbar hidden');
assert.strictEqual(pure.viewport.classes.has('with-rename-label'), false);
assert.strictEqual(pure.viewport.children.length, 1);
const pureWrap = pure.viewport.children[0];
assert.strictEqual(pureWrap.children.length, 2,
  'the single-image view appends the label and the image, not just one of them');
assert.strictEqual(pureWrap.children[0].className, 'renamed-label');
assert.ok(pureWrap.children[0].innerHTML.includes('old/a.bmp'));
assert.strictEqual(pureWrap.children[1].src, B.src, 'the head image is the one shown');

// The map it asked for, and only one of them — the gutter's map is skipped when
// it is not going to be drawn, which is the PR's performance claim.
assert.deepStrictEqual(pure.calls.rowMaps, [{ threshold: 0, includeAlpha: true }],
  'a pure rename costs one full-image pass, at the exact threshold, with alpha');

// -- A rename that also changed keeps the modes, with the label above them ----

const moved = render(RENAMED, A, B, differs);
assert.strictEqual(moved.calls.renamed, 0, 'a changed rename must not collapse to one image');
assert.deepStrictEqual(moved.calls.modes, ['side-by-side'], 'the current mode still renders');
assert.strictEqual(moved.viewport.classes.has('with-rename-label'), true,
  'the viewport must stack the label above the modes');
assert.strictEqual(moved.viewport.children.length, 1,
  'the label is appended to the viewport, not computed and dropped');
assert.strictEqual(moved.viewport.children[0].className, 'renamed-label');
assert.ok(moved.viewport.children[0].innerHTML.includes('old/a.bmp'));
assert.deepStrictEqual(moved.calls.rowMaps,
  [{ threshold: 0, includeAlpha: true }, { threshold: 10, includeAlpha: false }],
  'and the gutter still gets its own map, at the gutter tolerance, without alpha');

// -- Differing dimensions reach the same place, through the decision ---------

const resized = render(RENAMED, A, tall, same);
assert.strictEqual(resized.calls.renamed, 0,
  'a rename whose image changed size must not collapse, however clean the map');
assert.deepStrictEqual(resized.calls.modes, ['side-by-side']);
assert.strictEqual(resized.viewport.classes.has('with-rename-label'), true);

// -- A rename GitHub gave no previous path for still works -------------------

const noPrev = render({ path: 'new/a.bmp', status: 'renamed' }, A, B, same);
assert.strictEqual(noPrev.calls.renamed, 1);
assert.strictEqual(noPrev.viewport.children[0].children.length, 1,
  'no label to show, so only the image');

// -- An ordinary modification is untouched by any of this --------------------

const plain = render(MODIFIED, A, B, differs);
assert.strictEqual(plain.calls.renamed, 0);
assert.deepStrictEqual(plain.calls.modes, ['side-by-side']);
assert.strictEqual(plain.viewport.classes.has('with-rename-label'), false);
assert.strictEqual(plain.viewport.children.length, 0, 'and no rename label');
assert.deepStrictEqual(plain.calls.rowMaps, [{ threshold: 10, includeAlpha: false }],
  'a file that is not a rename pays for no extra pass');

// A modified file whose images happen to match is still a modification: this
// change is about renames and must not quietly collapse anything else.
const plainSame = render(MODIFIED, A, B, same);
assert.strictEqual(plainSame.calls.renamed, 0);
assert.deepStrictEqual(plainSame.calls.modes, ['side-by-side']);

// ── The shared label ────────────────────────────────────────────────────────

function labelFor(fileData, currentFile, escHtml) {
  const doc = { createElement: () => stubEl() };
  return new Function(
    'document', 'state', 'getFileData', 'escHtml',
    'return (' + bodyOf('renameLabel') + ');')(
    doc, { currentFile: currentFile }, () => fileData, escHtml || String)();
}

const label = labelFor({ previous_filename: 'old/a.bmp' }, 'new/a.bmp');
assert.strictEqual(label.className, 'renamed-label');
assert.ok(label.innerHTML.indexOf('old/a.bmp') < label.innerHTML.indexOf('new/a.bmp'),
  'old path first, new path second — the arrow points one way');

// GitHub can report a rename without a previous path (see buildImageUrls, which
// has the same fallback). Nothing to say then, and nothing rendered.
assert.strictEqual(labelFor({}, 'new/a.bmp'), null);
assert.strictEqual(labelFor(null, 'new/a.bmp'), null);

// Both values interpolated into that innerHTML come from the GitHub API, so
// both go through escHtml. Asserting only the previous path left the current
// one uncovered.
const escaped = labelFor(
  { previous_filename: '<img src=x onerror=1>' },
  '<svg onload=2>',
  s => String(s).replace(/</g, '&lt;'));
assert.ok(!escaped.innerHTML.includes('<img'), 'the previous path goes through escHtml');
assert.ok(!escaped.innerHTML.includes('<svg'), 'and so does the current path');

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
