// Exercises the "GitHub cut this list short" note that lives in
// static/index.html (#23).
//
// The flag it renders has a shape no test of the server can check: /images
// sets `truncated` when the walk over GitHub's file list hit the 3000-file
// ceiling, and until this the SPA read the key nowhere, so a cut-off list
// rendered exactly like a complete one. What makes that worth its own test
// file is that the failure is invisible — the page is not broken, it is
// merely wrong about how much of the PR it is showing.
//
// Three parts. The decision is a pure region, extracted and run. The renderer
// touches a DOM, so it is run against a stub one rather than matched by
// pattern — a regex that matches the text of an assignment passes whether or
// not the assignment does anything. The last part is the call site and the
// markup, which live outside any function this file can run.

const assert = require('assert');
const { extract, bodyOf, stylesheet, scriptSource } = require('./spa_source');

const spaScript = scriptSource();

// ── The decision ────────────────────────────────────────────────────────────

const truncationNotice = extract('truncation-note', 'truncationNotice');
const TRUNCATION_NOTE = extract('truncation-note', 'TRUNCATION_NOTE');

// The message itself. Asserted because every case below compares against this
// same constant, so all of them would pass with it set to the empty string —
// and an empty note renders as a blank warning strip that says nothing. This
// is a floor on the message existing and naming the thing, not an opinion
// about its wording: rewording it must not fail here.
assert.strictEqual(typeof TRUNCATION_NOTE, 'string',
  'the region must export a TRUNCATION_NOTE string');
assert.ok(TRUNCATION_NOTE.trim().length > 20,
  'the note must actually say something; got ' + JSON.stringify(TRUNCATION_NOTE));
assert.ok(/github/i.test(TRUNCATION_NOTE),
  'the note must say who cut the list short, so the reader knows it is not a bug here');

// A truncated list warns.
assert.strictEqual(truncationNotice(true), TRUNCATION_NOTE);

// A complete one says nothing. `false` is what the server sends; the rest are
// the ways the key can arrive absent or empty, and a response predating the
// flag must not be read as a warning.
for (const quiet of [false, undefined, null, 0, '', NaN]) {
  assert.strictEqual(truncationNotice(quiet), null,
    JSON.stringify(quiet) + ' is not a truncated list');
}

// Anything else truthy warns too. The server sends a boolean, so this decides
// nothing about the traffic it actually sees — it fixes the direction the
// function errs in, which is the same direction `_gh_paginate` errs in when it
// sets the flag: toward saying the list may be incomplete.
for (const loud of [1, 'true', 'false', {}, []]) {
  assert.strictEqual(truncationNotice(loud), TRUNCATION_NOTE,
    JSON.stringify(loud) + ' must err toward warning');
}

// ── The renderer ────────────────────────────────────────────────────────────
//
// Run for real against a stub element. The stub carries only `textContent` and
// `hidden`, so a renderer reaching for `innerHTML` — or for `style.display`,
// which the [hidden] rule below would then fight — fails here rather than
// passing on an element nothing checked.

function stubNote() {
  return { textContent: 'sentinel', hidden: false };
}

function render(truncated) {
  const el = stubNote();
  new Function('truncationNote', 'truncationNotice',
    'return (' + bodyOf('renderTruncationNote') + ');')(el, truncationNotice)(truncated);
  return el;
}

const warned = render(true);
assert.strictEqual(warned.textContent, TRUNCATION_NOTE);
assert.strictEqual(warned.hidden, false, 'a warning nobody can see is not a warning');

// The quiet case has to clear both, not just one. Leaving the text in place
// behind `hidden` is harmless; leaving `hidden` false with empty text paints an
// empty warning strip above the file list on every ordinary PR, which is most
// of them.
const quiet = render(false);
assert.strictEqual(quiet.textContent, '', 'the sentinel must be cleared, not left behind');
assert.strictEqual(quiet.hidden, true, 'an untruncated list must not reserve a strip');

// The renderer must ask the decision rather than re-deciding. Handed a notice
// function that answers for everything, the element says so — which fails if
// the renderer tests `truncated` itself and only consults the helper for text.
const viaHelper = stubNote();
new Function('truncationNote', 'truncationNotice',
  'return (' + bodyOf('renderTruncationNote') + ');')(viaHelper, () => 'ALWAYS')(false);
assert.strictEqual(viaHelper.textContent, 'ALWAYS',
  'renderTruncationNote must take its answer from truncationNotice');
assert.strictEqual(viaHelper.hidden, false);

// ── The call site and the markup ────────────────────────────────────────────
//
// A renderer that is correct and never called is the same bug, and it is the
// bug #23 describes: the flag was in the payload the whole time. Anchored to
// the argument, not just the callee — `renderTruncationNote(false)` would
// satisfy a bare-call check and reproduce the issue exactly.

assert.ok(/renderTruncationNote\s*\(\s*data\s*\.\s*truncated\s*\)/.test(spaScript),
  'loadPrImages must pass the /images payload\'s own truncated flag to the renderer');

// And it must be called where the payload is, beside the count it qualifies.
const loadBody = bodyOf('loadPrImages');
assert.ok(/renderTruncationNote\s*\(\s*data\s*\.\s*truncated\s*\)/.test(loadBody),
  'the call must be inside loadPrImages, where data is in scope');

// The element the renderer writes to, and the id it is found by. A renderer
// pointed at an element that does not exist throws on the first PR that needs
// it and on no other, which is the least testable moment to find out.
assert.ok(/id=["']file-list-truncated["']/.test(spaScript),
  'the sidebar must carry the #file-list-truncated element');
assert.ok(/getElementById\(\s*['"]file-list-truncated['"]\s*\)/.test(spaScript),
  'the SPA must look the note element up by that id');

// It starts hidden in the markup. Without this the strip is painted on first
// paint of every PR and only removed once /images answers — a warning that
// flashes on lists that are complete.
const markup = spaScript.match(/<div class="truncation-note"[^>]*>/);
assert.ok(markup, 'the note element must carry the truncation-note class');
assert.ok(/\bhidden\b/.test(markup[0]),
  'the note must start hidden: ' + markup[0]);

// ── The stylesheet ──────────────────────────────────────────────────────────
//
// Two rules, both load-bearing in a way no assertion above reaches.

const css = stylesheet();

// The colours come from the theme's own variables, so the strip is legible in
// both themes. A hard-coded pair is readable in whichever theme it was written
// in and not the other, and the repo's baselines are captured in both.
const rule = css.match(/\.truncation-note\s*\{[^}]*\}/);
assert.ok(rule, 'static/index.html must style .truncation-note');
assert.ok(/var\(--vr-[a-z-]+\)/.test(rule[0]),
  '.truncation-note must take its colours from the theme variables: ' + rule[0]);
assert.ok(/background\s*:\s*var\(/.test(rule[0]) && /color\s*:\s*var\(/.test(rule[0]),
  '.truncation-note must set both a themed background and a themed foreground');

// The [hidden] rule is not redundant with the UA stylesheet. It is insurance
// against a later `display:` on the class itself: an author-level display wins
// over the UA's [hidden] rule, and the strip would then show on every PR with
// the renderer still doing exactly what it does above.
assert.ok(/\.truncation-note\[hidden\]\s*\{\s*display\s*:\s*none/.test(css),
  '.truncation-note[hidden] must force display:none over any display on the class');

console.log('truncation note: ok');
