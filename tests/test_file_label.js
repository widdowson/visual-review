// Exercises the sidebar's label for a file — the decision about how much of a
// path a row has to show, and the rendering that acts on it. Both live in
// static/index.html.
//
// Issue #31: renderFileList labelled every row with the file's basename and
// put the whole path in the title, so a PR carrying one basename in two
// directories — the normal shape of apwphotos-appv2's per-app baseline
// directories — showed two rows reading `cuj_01_login.bmp`, distinguishable
// only by hovering each one. A three-way collision read as three identical
// rows. After #24 clicking either row selects the right file, so nothing was
// wrong; it was unreadable.
//
// Three parts. The decision is a pure region, extracted and run. renderFileList
// touches a DOM, so it is run against stubs rather than matched by pattern — a
// regex that matches the text of a call passes whether or not the call does
// anything. The last part reads the stylesheet, for a rule whose absence no
// assertion on the markup can see.

const assert = require('assert');
const { extract, bodyOf, stylesheet } = require('./spa_source');

const dirContextFor = extract('hash-target', 'dirContextFor');
const basenameOf = extract('hash-target', 'basenameOf');

const files = paths => paths.map(p => ({path: p, status: 'modified'}));

// The payload the issue describes: two per-app baseline directories carrying
// the same three-level tail, so the segment that tells them apart is `home` vs
// `shoots` — four segments up.
const BASELINES = files([
  'django/apps/home/tests/visual/cuj_01_login.bmp',
  'django/apps/home/tests/visual/cuj_02_dashboard.bmp',
  'django/apps/shoots/tests/visual/cuj_01_login.bmp',
]);

// ── The decision ────────────────────────────────────────────────────────────

// The common case is no second line at all. A basename no other file shares
// needs no context, and drawing a directory under every row would cost the
// sidebar half its height for nothing.
assert.strictEqual(dirContextFor(BASELINES[1].path, BASELINES), null,
  'a file whose basename nothing shares must want no context');
assert.strictEqual(dirContextFor('a/b/c.png', files(['a/b/c.png'])), null,
  'a file is not a collision with itself');
assert.strictEqual(dirContextFor('x/y.png', BASELINES), null,
  'a path absent from the list is shadowed by nothing in it');
assert.strictEqual(dirContextFor('x/y.png', []), null);

// A collision gets the shortest trailing run of directories that separates it
// from its partner — not the whole path, which does not fit the column, and
// not one segment, which here would read `visual/` under both rows and be a
// second way of showing the same thing twice.
assert.strictEqual(
  dirContextFor(BASELINES[0].path, BASELINES), 'home/tests/visual',
  'the context must reach back to the segment that settles it');
assert.strictEqual(
  dirContextFor(BASELINES[2].path, BASELINES), 'shoots/tests/visual',
  'and must do so for both members of the collision, not just the later one');

// One segment is enough where one segment differs, which is the point of
// taking the shortest: `a/` and `b/` rather than two paths the column cuts off
// at a common prefix.
const SHALLOW = files(['a/name.png', 'b/name.png']);
assert.strictEqual(dirContextFor('a/name.png', SHALLOW), 'a');
assert.strictEqual(dirContextFor('b/name.png', SHALLOW), 'b');

// The answer's leftmost segment is the one that settles it — at one segment
// shorter, something still matched. That is what makes an ellipsis at the
// right end of the line the correct one: what a narrow column truncates is the
// tail every candidate has in common.
for (const img of BASELINES) {
  const context = dirContextFor(img.path, BASELINES);
  if (context === null) continue;
  const shorter = context.split('/').slice(1).join('/');
  const rivals = BASELINES.filter(
    o => o.path !== img.path && basenameOf(o.path) === basenameOf(img.path));
  assert.ok(
    rivals.some(o => ('/' + o.path).endsWith('/' + shorter + '/' + basenameOf(o.path))),
    'one segment shorter must still collide, or the answer was not the shortest');
}

// Three rows, not two: each must be told apart from both others, so a suffix
// that separates it from one of them is not enough.
const THREE = files(['p/q/name.png', 'r/q/name.png', 's/q/name.png']);
assert.deepStrictEqual(
  THREE.map(img => dirContextFor(img.path, THREE)), ['p/q', 'r/q', 's/q'],
  'a three-way collision must separate each row from both others');

// A rival with fewer directory segments than the run being tried cannot match
// it, so it is distinguished rather than skipped — the case where one file is
// nested deeper than the other.
const UNEVEN = files(['q/name.png', 'p/q/name.png']);
assert.strictEqual(dirContextFor('p/q/name.png', UNEVEN), 'p/q',
  'a shorter rival must not be read as sharing the longer suffix');

// ── The repository root is an answer, not the absence of one ────────────────
//
// A file at the root colliding with a nested one has no directory to show, and
// being at the root is exactly what distinguishes it. '' says that; null would
// say "no context needed" and put the row back to being indistinguishable,
// which is the bug.
const ROOT_COLLIDING = files(['name.png', 'dir/name.png']);
assert.strictEqual(dirContextFor('name.png', ROOT_COLLIDING), '',
  'a colliding root-level file must report the root, not no context');
assert.strictEqual(dirContextFor('dir/name.png', ROOT_COLLIDING), 'dir');

// The other way no suffix is its own: a rival nested deeper whose tail is this
// file's whole directory. `b/c` is a suffix of `a/b/c`, so every run the
// shallower file can try is shared, and its answer is its whole directory —
// which still reads differently from the deeper file's.
const NESTED = files(['b/c/name.png', 'a/b/c/name.png']);
assert.strictEqual(dirContextFor('b/c/name.png', NESTED), 'b/c',
  'a file whose directory is a tail of its rival must show that directory');
assert.strictEqual(dirContextFor('a/b/c/name.png', NESTED), 'a/b/c',
  'and the deeper file must reach past it to the segment that settles it');

// ── The rendering ───────────────────────────────────────────────────────────
//
// renderFileList is DOM-bound, so it is run rather than read: every name it
// closes over is supplied, and the rows it builds are inspected. A name it
// starts closing over and this file does not supply is a ReferenceError naming
// it, which is a loud failure pointing here.

function element(tag) {
  const el = {
    tag: tag, className: '', title: '', textContent: '',
    attrs: {}, children: [], listeners: [],
  };
  // innerHTML drops the children, as a browser's does. A plain '' property
  // here made the check that the list is cleared one that could not fail: the
  // stub started empty, so it read '' whether or not anything cleared it.
  let html = '';
  Object.defineProperty(el, 'innerHTML', {
    get: () => html,
    set: v => { html = v; if (v === '') el.children.length = 0; },
  });
  el.setAttribute = (k, v) => { el.attrs[k] = v; };
  el.getAttribute = k => (k in el.attrs ? el.attrs[k] : null);
  el.appendChild = child => { el.children.push(child); return child; };
  el.addEventListener = (evt, fn) => { el.listeners.push([evt, fn]); };
  return el;
}

function render(images) {
  const state = {images: images};
  const fileList = element('ul');
  const document = {createElement: element};
  const selected = [];

  // A row left over from a previous render, so that every case below is also
  // a case about clearing the list. renderFileList is called again whenever
  // the file list is rebuilt, and a stale row here shows up as a row with no
  // file-label block.
  fileList.innerHTML = '<li>stale</li>';
  fileList.children.push(element('li'));

  new Function(
    'state', 'fileList', 'document', 'selectFile', 'basenameOf', 'dirContextFor',
    bodyOf('renderFileList') + '\nreturn renderFileList;')(
      state, fileList, document, p => selected.push(p), basenameOf, dirContextFor)();

  const rows = fileList.children.map(function(li) {
    const label = li.children.find(c => c.className === 'file-label');
    assert.ok(label, 'every row must carry a file-label block');
    const dir = label.children.find(c => c.className === 'file-dir');
    return {
      li: li,
      path: li.getAttribute('data-path'),
      name: label.children.find(c => c.className === 'filename').textContent,
      dir: dir ? dir.textContent : null,
      title: label.title,
    };
  });
  return {rows: rows, selected: selected, fileList: fileList};
}

// The issue, as the sidebar shows it: the two colliding rows now read
// differently, and the uncollided one is untouched.
{
  const r = render(BASELINES).rows;
  assert.deepStrictEqual(r.map(x => x.name),
    ['cuj_01_login.bmp', 'cuj_02_dashboard.bmp', 'cuj_01_login.bmp'],
    'the filename stays the primary label on every row');
  assert.deepStrictEqual(r.map(x => x.dir),
    ['home/tests/visual/', null, 'shoots/tests/visual/'],
    'only the colliding rows get a directory line, and they differ');
  assert.notStrictEqual(r[0].dir, r[2].dir,
    'two rows reading the same filename must not read the same overall');
}

// The context is computed against the whole list, not against the row. Pass
// each image its own one-file list and every collision disappears — which is
// the shape this bug had in the first place.
{
  const r = render(files(['a/name.png', 'b/name.png'])).rows;
  assert.deepStrictEqual(r.map(x => x.dir), ['a/', 'b/'],
    'a collision must be found against the full image list');
}

// The trailing separator is what makes the line read as a directory rather
// than as a second filename, and the root case says './' rather than nothing —
// a blank line there would look exactly like the unambiguous case.
{
  const r = render(ROOT_COLLIDING).rows;
  assert.deepStrictEqual(r.map(x => x.dir), ['./', 'dir/'],
    'a root-level collision must be shown as ./ rather than as no line at all');
}

// The whole path stays reachable on hover, on the block rather than on the
// filename alone, so pointing at either line answers.
{
  const r = render(BASELINES).rows;
  assert.deepStrictEqual(r.map(x => x.title), BASELINES.map(i => i.path),
    'the full path must stay in the title of the label block');
  assert.deepStrictEqual(r.map(x => x.path), BASELINES.map(i => i.path),
    'and data-path must still name the file, which is what selection reads');
}

// Clicking a row still selects that row's file. The label block is new markup
// between the row and its text, and the click handler is on the row.
{
  const r = render(BASELINES);
  r.rows.forEach(function(row) {
    const click = row.li.listeners.find(([evt]) => evt === 'click');
    assert.ok(click, 'every row must still listen for a click');
    click[1]();
  });
  assert.deepStrictEqual(r.selected, BASELINES.map(i => i.path),
    'each row must still select its own file');
}

// The list is emptied before it is rebuilt, so rows do not stack up. Every
// case above renders over a stale row; this one says so out loud.
{
  const r = render(BASELINES);
  assert.strictEqual(r.fileList.innerHTML, '',
    'the list must be cleared before it is rebuilt');
  assert.strictEqual(r.fileList.children.length, BASELINES.length,
    'and no row from the previous render may survive it');
}

// ── The rule that makes the second line a second line ───────────────────────
//
// Markup alone cannot show this: an unstyled .file-dir inherits the row's font
// size and colour, so the directory renders as a second filename — louder than
// the collision it explains. And .file-label is a flex column inside a flex
// row, where the default min-width:auto refuses to shrink below the content,
// so without the override a long path pushes the row wider than the sidebar
// instead of ellipsizing.
{
  const css = stylesheet();
  const rule = name => {
    const at = css.indexOf('.file-item .' + name + ' {');
    assert.ok(at >= 0, 'the stylesheet must carry a .file-item .' + name + ' rule');
    return css.slice(at, css.indexOf('}', at));
  };

  const dir = rule('file-dir');
  assert.ok(/font-size\s*:/.test(dir), '.file-dir must set its own font size');
  assert.ok(/color\s*:/.test(dir), '.file-dir must set its own colour');
  assert.ok(/text-overflow\s*:\s*ellipsis/.test(dir),
    '.file-dir must ellipsize; these paths are longer than the column');

  const label = rule('file-label');
  assert.ok(/flex-direction\s*:\s*column/.test(label),
    '.file-label must stack its two lines');
  assert.ok(/min-width\s*:\s*0/.test(label),
    '.file-label must be allowed to shrink, or neither line can ellipsize');
}

console.log('test_file_label: all checks passed');
