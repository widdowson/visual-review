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
// anything. The last part reads the stylesheet, for two rules whose absence no
// assertion on the markup can see.

const assert = require('assert');
const { extract, bodyOf, stylesheet } = require('./spa_source');

const dirContextFor = extract('hash-target', 'dirContextFor');
const dirLabelFor = extract('hash-target', 'dirLabelFor');
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

// The answer is the shortest run that separates the row, checked as the
// property rather than as the algorithm: nothing shares the run it returned,
// and every shorter run is shared by something. The implementation walks
// lengths upward and returns the first that is free; this walks the answer it
// got and checks both halves of what "shortest" means.
//
// That second half is empty for a one-segment answer, which is not a gap: the
// only shorter run is the empty one, which every file shares by construction —
// it is why the loop starts at one segment rather than at none. An earlier
// version of this check wrote the shorter run as an endsWith against '',
// which is false for every path, so it would have fired on correct code; it
// was green only because it ran over a fixture whose answers were all three
// segments long. It runs over every fixture in this file now.
const dirsOf = path => path.split('/').slice(0, -1);
const runOf = (segs, k) => segs.slice(Math.max(0, segs.length - k)).join('/');

function shortestAnswer(img, images) {
  const context = dirContextFor(img.path, images);
  if (context === null) return;
  const rivals = images.filter(
    o => o.path !== img.path && basenameOf(o.path) === basenameOf(img.path));
  assert.ok(rivals.length, 'a context was answered for a file nothing collides with');

  // A root-level file has no run to return, so '' is the whole answer and
  // there is nothing to measure. That it is distinguishing at all is the
  // ROOT_COLLIDING case below.
  if (context === '') {
    assert.deepStrictEqual(dirsOf(img.path), [],
      "'' must mean the repository root, not an empty answer for a nested file");
    return;
  }

  const own = dirsOf(img.path);
  const take = context.split('/').length;
  assert.strictEqual(runOf(own, take), context,
    'the answer must be a trailing run of this file\'s own directory');

  // Every shorter run is shared — that is minimality, and it is what the
  // answer being the leftmost differing segment rests on.
  for (let k = 1; k < take; k++) {
    assert.ok(rivals.some(o => runOf(dirsOf(o.path), k) === runOf(own, k)),
      'a shorter run must still be shared, or the answer was not the shortest: '
      + img.path + ' -> ' + JSON.stringify(context) + ' at ' + k);
  }

  // And the answer itself is shared with nothing — unless it is the whole
  // directory, which is the fallback for a file every one of whose runs a
  // deeper rival shares (`q/name.png` under `p/q/name.png`). There the run is
  // shared and the rows are still told apart, because one prints a whole
  // directory and the other a longer one. Which is why uniqueness is asserted
  // on the printed label rather than here.
  const free = !rivals.some(o => runOf(dirsOf(o.path), take) === context);
  assert.ok(free || take === own.length,
    'a shared answer is only allowed as the whole-directory fallback: '
    + img.path + ' -> ' + context);
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

// A file directly inside its rival's directory, for the display decision
// below. Declared here rather than beside its own assertions so that the
// fixture list is genuinely every fixture in the file — one declared after it
// escapes both property checks while the comment there says it cannot.
const INSIDE = files(['a/name.png', 'a/b/name.png']);
assert.strictEqual(dirContextFor('b/c/name.png', NESTED), 'b/c',
  'a file whose directory is a tail of its rival must show that directory');
assert.strictEqual(dirContextFor('a/b/c/name.png', NESTED), 'a/b/c',
  'and the deeper file must reach past it to the segment that settles it');

// Every fixture in this file, now that they are all declared — the check above
// is only worth having if it runs over the shallow and root-level answers too.
const FIXTURES = [BASELINES, SHALLOW, THREE, UNEVEN, ROOT_COLLIDING, NESTED, INSIDE];
for (const images of FIXTURES) {
  for (const img of images) shortestAnswer(img, images);
}

// The property the whole feature exists for, stated once over every fixture:
// two rows sharing a basename never print the same thing. The per-answer
// checks above are about how the answer is chosen; this is about whether the
// sidebar is readable, which is the issue.
for (const images of FIXTURES) {
  const labels = new Map();
  for (const img of images) {
    const key = basenameOf(img.path) + '\u0000' + dirLabelFor(img.path, images);
    assert.ok(!labels.has(key),
      'two rows must not read the same: ' + img.path + ' and ' + labels.get(key)
      + ' both print ' + JSON.stringify(dirLabelFor(img.path, images)));
    labels.set(key, img.path);
  }
}

// The same property over generated lists, because the fixtures above are the
// shapes I thought of. A seeded generator, so a failure is reproducible: an
// alphabet of three segment names over depths 0 to 3 forces collisions,
// root-level files and shared tails at a rate hand-written cases do not.
// #24's review used the same device on the hash round trip.
{
  // xorshift32, not an LCG: a textbook LCG's low bits have a short period, so
  // `seed % n` for a small n returns almost the same value every time. The
  // first draft of this block did that and generated 4,086 files across 4,000
  // lists — barely one apiece, and the collision counter below is here so that
  // degrading like that again fails instead of passing quietly.
  let seed = 0x2f6e2b1;
  const rand = n => {
    seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5; seed |= 0;
    return (seed >>> 0) % n;
  };
  const SEGS = ['a', 'b', 'c'];
  let lists = 0, rows = 0, collided = 0;
  for (let t = 0; t < 4000; t++) {
    const seen = new Set();
    for (let f = 0, n = 1 + rand(5); f < n; f++) {
      const dirs = [];
      for (let d = 0, depth = rand(4); d < depth; d++) dirs.push(SEGS[rand(SEGS.length)]);
      seen.add(dirs.concat(['name' + rand(2) + '.png']).join('/'));
    }
    const images = files([...seen]);
    const labels = new Map();
    for (const img of images) {
      const key = basenameOf(img.path) + '\u0000' + dirLabelFor(img.path, images);
      assert.ok(!labels.has(key),
        'generated list printed two identical rows: ' + img.path + ' and '
        + labels.get(key) + ' in ' + JSON.stringify(images.map(i => i.path)));
      labels.set(key, img.path);
      shortestAnswer(img, images);

      // The contract the render site reads: null means no line, and every
      // other answer is a non-empty string. That is what makes the site's
      // `!== null` and a bare truthiness test the same thing today — so
      // swapping them is an equivalent mutant, and this is what keeps it one.
      const printed = dirLabelFor(img.path, images);
      assert.ok(printed === null || (typeof printed === 'string' && printed !== ''),
        'dirLabelFor must answer null or a non-empty string, not '
        + JSON.stringify(printed) + ' for ' + img.path);
      if (printed !== null) collided++;
      rows++;
    }
    lists++;
  }
  assert.ok(lists === 4000 && rows > 10000,
    'the generator must actually have produced lists to check: ' + lists + '/' + rows);
  assert.ok(collided > rows / 10,
    'most of this block is wasted unless the lists really do collide: '
    + collided + ' colliding rows out of ' + rows);
}

// ── What the row actually prints ────────────────────────────────────────────
//
// A run of segments rendered as if it were a path is a lie no reader can see
// through, and the root case is what teaches them to read it that way: `./` is
// genuinely root-relative, so an unmarked `b/` beside it reads as a sibling of
// `a/` rather than as a directory inside it.

// A whole directory prints as itself.
assert.strictEqual(dirLabelFor('a/name.png', SHALLOW), 'a/');
assert.strictEqual(dirLabelFor('dir/name.png', ROOT_COLLIDING), 'dir/');
assert.strictEqual(dirLabelFor('b/c/name.png', NESTED), 'b/c/');
assert.strictEqual(dirLabelFor('a/b/c/name.png', NESTED), 'a/b/c/');

// A tail of one is marked, so it cannot be read as the whole path.
assert.deepStrictEqual(
  INSIDE.map(i => dirLabelFor(i.path, INSIDE)), ['a/', '\u2026/b/'],
  'a file inside its rival must not print as a sibling of it');
assert.strictEqual(
  dirLabelFor(BASELINES[0].path, BASELINES), '\u2026/home/tests/visual/',
  'a suffix of a longer directory must carry the ellipsis');

// The root is the root, and the ordinary case still prints nothing.
assert.strictEqual(dirLabelFor('name.png', ROOT_COLLIDING), './');
assert.strictEqual(dirLabelFor(BASELINES[1].path, BASELINES), null);

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
    'state', 'fileList', 'document', 'selectFile', 'basenameOf', 'dirLabelFor',
    bodyOf('renderFileList') + '\nreturn renderFileList;')(
      state, fileList, document, p => selected.push(p), basenameOf, dirLabelFor)();

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
    ['\u2026/home/tests/visual/', null, '\u2026/shoots/tests/visual/'],
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

// ── The rules that make the second line a second line ───────────────────────
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
  // text-overflow does nothing to text that is allowed to wrap, so without
  // this the long directory takes a second row instead of an ellipsis.
  assert.ok(/white-space\s*:\s*nowrap/.test(dir),
    '.file-dir must not wrap, or text-overflow has nothing to act on');

  const label = rule('file-label');
  assert.ok(/flex-direction\s*:\s*column/.test(label),
    '.file-label must stack its two lines');
  // flex-direction is inert on a block, so asserting it without this asserts
  // nothing: the filename and the directory would render on one line.
  assert.ok(/display\s*:\s*flex/.test(label),
    '.file-label must be a flex container, or flex-direction does nothing');
  assert.ok(/min-width\s*:\s*0/.test(label),
    '.file-label must be allowed to shrink, or neither line can ellipsize');
  // This PR moved `flex: 1` off .filename onto this block. Without it the
  // block sizes to its content and the comment badge slides in beside the
  // filename instead of sitting at the right edge of the row.
  assert.ok(/flex\s*:\s*1/.test(label),
    '.file-label must take the free space .filename used to take');
}

console.log('test_file_label: all checks passed');
