// Exercises the comments-pane arithmetic that lives in static/index.html.
//
// The SPA is one file by design, so the two functions are extracted from it
// here rather than copied: a test holding its own copy of the logic would keep
// passing after the real one changed. The markers are part of the contract —
// if they go missing this test fails rather than silently testing nothing.

const assert = require('assert');
const { extract, bodyOf, stylesheet, scriptSource } = require('./spa_source');

const computeCommentsPaneHeight = extract('comments-pane', 'computeCommentsPaneHeight');
const computeCommentsScrollTop = extract('comments-pane', 'computeCommentsScrollTop');

// ── The shipped tuning ──────────────────────────────────────────────────────
// Asserted because every case below passes explicit arguments, so all of them
// would still pass with the feature switched off in the SPA. Each assertion is
// a floor on the feature existing, not an opinion about its tuning: raising
// either number is a judgement call and must not fail here. The loop checks
// only that each value is an integer, so the two floors below are reachable —
// an `isInteger(v) && v > 0` loop would answer for them first and leave them
// dead.

const tuning = extract('comments-pane', 'COMMENTS_PANE_TUNING');
assert.strictEqual(typeof tuning, 'object', 'the region must export a COMMENTS_PANE_TUNING object');
assert.deepStrictEqual(
  Object.keys(tuning).sort(), ['minPaneHeight', 'minViewportHeight'],
  'COMMENTS_PANE_TUNING gained or lost a key; the assertions below need updating');
for (const [k, v] of Object.entries(tuning)) {
  assert.ok(Number.isInteger(v), 'COMMENTS_PANE_TUNING.' + k + ' must be an integer, got ' + v);
}
assert.ok(tuning.minPaneHeight > 0, 'a dragged-shut pane must keep a draggable edge');
assert.ok(tuning.minViewportHeight > 0,
  'the image comparator must keep a reserve however far the pane is dragged');

// ── The SPA must use all of it ──────────────────────────────────────────────
// The cases further down pass explicit arguments to the two pure functions, so
// none of them would notice the SPA hard-coding a value, or calling neither
// function at all. These checks are structural because the code they cover
// touches the DOM and so cannot be extracted and run. Each one is anchored to
// the *call site* as well as the callee, since the line this PR replaced lived
// at a call site: a helper that is correct and never called is the same bug.

const resizeBody = bodyOf('resizeCommentsPane');
// Each knob is matched with the option it fills, not on its own — with bare
// tokens, `minPaneHeight: COMMENTS_PANE_TUNING.minViewportHeight` would keep
// both present and pass.
for (const key of ['minPaneHeight', 'minViewportHeight']) {
  assert.ok(
    new RegExp(key + '\\s*:\\s*COMMENTS_PANE_TUNING\\s*\\.\\s*' + key + '\\b').test(resizeBody),
    'resizeCommentsPane must pass COMMENTS_PANE_TUNING.' + key + ' as the clamp\'s ' + key);
}
assert.ok(/computeCommentsPaneHeight\s*\(/.test(resizeBody),
  'resizeCommentsPane must apply the clamp rather than setting a raw height');

// ...and the drag must reach it. The handler is named in the SPA precisely so
// this can read it: emptied out, every assertion above stays green while the
// handle does nothing.
const dragBody = bodyOf('onCommentsDragMove');
assert.ok(/resizeCommentsPane\s*\(/.test(dragBody),
  'the drag handler must call resizeCommentsPane');
assert.ok(/addEventListener\s*\(\s*'mousemove'\s*,\s*onCommentsDragMove\s*\)/.test(scriptSource()),
  'onCommentsDragMove must be registered on mousemove');

const scrollBody = bodyOf('scrollToNewestComment');
assert.ok(/computeCommentsScrollTop\s*\(/.test(scrollBody),
  'scrollToNewestComment must use computeCommentsScrollTop');
// Matched with the value each one must carry, not merely present: `scrollTop: 0`
// satisfies a bare-key check and mis-parks every re-render of a scrolled pane.
for (const [key, value] of [
  ['scrollTop', 'commentsScroll.scrollTop'],
  ['clientHeight', 'commentsScroll.clientHeight'],
  ['scrollHeight', 'commentsScroll.scrollHeight'],
  ['newestCardBottom', 'newestCardBottom'],
]) {
  assert.ok(
    new RegExp(key + '\\s*:\\s*' + value.replace(/\./g, '\\s*\\.\\s*') + '\\b').test(scrollBody),
    'scrollToNewestComment must pass ' + value + ' as computeCommentsScrollTop\'s ' + key);
}

// ...and the render must call it. Reverting this PR means putting
// `scrollTop = scrollHeight` back here, where it was, not inside the helper.
const renderBody = bodyOf('renderComments');
assert.ok(/scrollToNewestComment\s*\(/.test(renderBody),
  'renderComments must park the newest comment at the bottom of the pane');
for (const [label, body] of [['renderComments', renderBody], ['scrollToNewestComment', scrollBody]]) {
  assert.ok(!/scrollTop\s*=\s*\w+\.scrollHeight\b/.test(body),
    label + ' must not scroll to the very bottom, which is the comment form');
}

// ── The initial cap is CSS, so assert it is still there ─────────────────────
// computeCommentsPaneHeight governs the drag; the height the pane opens at is
// the `max-height` on .comments-section. Without this the initial cap could be
// deleted with every case below still green.
const css = stylesheet();

// Rules whose selector is exactly `.comments-section`, and which are not nested
// in an @media (or @supports) block. Both halves matter: a cap that lives only
// in the mobile query leaves every desktop window uncapped, which is the bug
// this fixes, and a cap on `.some-wrapper .comments-section` applies to nothing
// this page renders. Depth is counted rather than matched, since a nested block
// is a brace inside a brace and a regex cannot tell the two apart; the selector
// is split on `,` so a grouped rule counts whichever position it appears in.
function unconditionalRules(sheet, selector) {
  const out = [];
  let depth = 0;
  let ruleStart = 0;
  let selectorStart = 0;
  for (let i = 0; i < sheet.length; i++) {
    if (sheet[i] === '{') {
      if (depth === 0) ruleStart = i;
      depth++;
    } else if (sheet[i] === '}') {
      depth--;
      if (depth !== 0) continue;
      // A rule whose own opening brace was at depth 0 is not inside @media.
      const selectors = sheet.slice(selectorStart, ruleStart).split(',').map((s) => s.trim());
      if (selectors.includes(selector)) out.push(sheet.slice(ruleStart, i + 1));
      selectorStart = i + 1;
    }
  }
  return out;
}

const paneRules = unconditionalRules(css, '.comments-section');
assert.ok(paneRules.length,
  'static/index.html must style .comments-section outside any @media block');
const caps = paneRules
  .map((rule) => rule.match(/max-height:\s*([\d.]+)vh/))
  .filter(Boolean);
assert.strictEqual(caps.length, 1,
  '.comments-section must cap its initial height in vh, unconditionally and ' +
  'in exactly one rule');
assert.ok(Number(caps[0][1]) > 0 && Number(caps[0][1]) <= 50,
  'the initial cap must be no more than half the window — the column is shorter ' +
  'than the window, so half of it is already the minority of the column — got ' +
  caps[0][1] + 'vh');
// The body must be the thing that scrolls: a pane that scrolls as a whole puts
// its drag handle over whatever comment it is scrolled against. Checked over
// every .comments-section rule, nested ones included.
assert.ok(/\.comments-scroll\s*\{[^}]*overflow-y:\s*auto/.test(css),
  '.comments-scroll must be the scrolling element');
for (const rule of css.match(/\.comments-section\s*\{[^}]*\}/g) || []) {
  assert.ok(!/overflow-y:\s*auto/.test(rule),
    '.comments-section must not scroll itself; .comments-scroll does');
}

// ── The drag clamp ──────────────────────────────────────────────────────────
// A 900px window with the comparator starting 134px down, which is what the
// real page measures at 1440×900: a 270px pane and a 496px comparator under a
// 900px window, and independently the 646px pane and 120px comparator the drag
// reaches at its ceiling.
function height(overrides) {
  return computeCommentsPaneHeight(Object.assign({
    paneBottom: 900, viewportTop: 134, pointerY: 600,
    minPaneHeight: 60, minViewportHeight: 120,
  }, overrides));
}

// The ceiling those numbers imply: 900 - 134 - 120.
const CEILING = 646;

assert.strictEqual(height({}), 300, 'an unclamped drag is the distance dragged');
assert.strictEqual(height({ pointerY: 254 }), CEILING, 'a drag exactly to the ceiling reaches it');
assert.strictEqual(height({ pointerY: 253 }), CEILING, 'a drag past the ceiling stops at it');
assert.strictEqual(height({ pointerY: 0 }), CEILING, 'a drag to the top of the window stops at it');
assert.strictEqual(height({ pointerY: -500 }), CEILING, 'a pointer dragged off the top stops at it');
assert.strictEqual(height({ pointerY: 840 }), 60, 'a drag exactly to the floor reaches it');
assert.strictEqual(height({ pointerY: 890 }), 60, 'a drag past the floor stops at it');
assert.strictEqual(height({ pointerY: 2000 }), 60, 'a pointer dragged off the bottom stops at it');

// The point of the ceiling: the comparator keeps its reserve at every pointer
// position, which is what a fraction of the window did not do — 80% of a 900px
// window is 720px, leaving the comparator 46px of the 766px column.
for (let pointerY = -200; pointerY <= 1100; pointerY += 7) {
  const h = height({ pointerY });
  assert.ok(h >= 60, 'the pane never goes below its floor, at pointerY ' + pointerY);
  assert.ok(900 - 134 - h >= 120,
    'the comparator keeps 120px at pointerY ' + pointerY + ' (pane was ' + h + ')');
}

// Dragging up never shrinks the pane and dragging down never grows it.
let previous = 0;
for (let pointerY = 1100; pointerY >= -200; pointerY -= 13) {
  const h = height({ pointerY });
  assert.ok(h >= previous, 'dragging up shrank the pane at pointerY ' + pointerY);
  previous = h;
}

// A window too short to honour both floors gives up the comparator's reserve
// rather than the pane's minimum, so the handle stays draggable instead of the
// clamp inverting and pinning the pane to a negative height. That outcome comes
// from applying the floor after the ceiling; there is no branch in the SPA for
// it, because a branch raising the ceiling first would be one no input could
// tell from its absence.
assert.strictEqual(
  height({ paneBottom: 200, viewportTop: 100, pointerY: 0 }), 60,
  'a column shorter than both floors still yields a pane at its minimum');
assert.strictEqual(
  height({ paneBottom: 200, viewportTop: 100, pointerY: 199 }), 60,
  'and a drag to the bottom of that column yields the same');

// The tuning is read from the arguments, not baked in.
assert.strictEqual(height({ minPaneHeight: 200, pointerY: 890 }), 200,
  'the floor comes from minPaneHeight');
assert.strictEqual(height({ minViewportHeight: 400, pointerY: 0 }), 366,
  'the ceiling comes from minViewportHeight');

// ── The initial scroll position ─────────────────────────────────────────────
// Measured from the real page at 1440×900 on apwphotos-appv2 PR #209's
// cuj_proofing_05_quota_progress.png, which has six comments: a 263px scroll
// body over 854px of content, the newest card ending 699px in, and the
// post-a-comment form with the pane's bottom padding taking the last 155px.
// newestCardBottom is measured from the container's top edge, so on a freshly
// rendered pane — scrollTop 0 — it is already a content coordinate.
function scrollTop(overrides) {
  return computeCommentsScrollTop(Object.assign({
    newestCardBottom: 699, scrollTop: 0, clientHeight: 263, scrollHeight: 854,
  }, overrides));
}

// 436 is what the real page lands on, measured.
assert.strictEqual(scrollTop({}), 436, 'the newest comment ends at the bottom of the pane');
assert.ok(scrollTop({}) < 854 - 263,
  'the pane must not be scrolled to its very bottom, which is the comment form');

// The card rect is relative to the container, so an already-scrolled container
// has to add its offset back. On the one path that ships today this is a no-op:
// loadComments writes a short spinner before the fetch, and that resets the
// scroll offset (measured in Chromium, 436 -> 0). The term and the guard below
// are what keep the helper correct for a caller that does not, since replacing
// the content on its own does not reset it (measured, 300 -> 300) — so they are
// the helper's contract rather than a fix for an observed defect, and these
// cases are what hold them to it.
assert.strictEqual(
  computeCommentsScrollTop({ newestCardBottom: 263, scrollTop: 472, clientHeight: 263, scrollHeight: 854 }),
  472, 'a card already parked at the fold in a scrolled pane stays where it is');
assert.strictEqual(scrollTop({ newestCardBottom: 100, scrollTop: 400 }), 237,
  'the scroll offset is added to the measured rect');

assert.strictEqual(scrollTop({ newestCardBottom: 0 }), 0,
  'a file with no comments shows the top of the pane');
assert.strictEqual(scrollTop({ newestCardBottom: 0, scrollTop: 500 }), 0,
  'and does so even when the pane was left scrolled by the previous file');
assert.strictEqual(scrollTop({ newestCardBottom: -20, scrollTop: 500 }), 0,
  'a card scrolled entirely above the container reads as nothing to park');

assert.strictEqual(
  computeCommentsScrollTop({ newestCardBottom: 150, scrollTop: 0, clientHeight: 263, scrollHeight: 254 }), 0,
  'content shorter than the pane does not scroll');
assert.strictEqual(scrollTop({ newestCardBottom: 200 }), 0,
  'a newest comment that already fits above the fold does not scroll');
assert.strictEqual(scrollTop({ newestCardBottom: 263 }), 0,
  'a newest comment ending exactly at the fold does not scroll');
assert.strictEqual(scrollTop({ newestCardBottom: 264 }), 1,
  'one pixel past the fold scrolls one pixel');
assert.strictEqual(scrollTop({ newestCardBottom: 854 }), 591,
  'a newest comment ending at the content bottom scrolls all the way');
assert.strictEqual(scrollTop({ newestCardBottom: 5000 }), 591,
  'a bottom past the content clamps to the last scrollable pixel');

// A pane dragged tall enough to hold everything shows everything from the top.
assert.strictEqual(scrollTop({ clientHeight: 900 }), 0,
  'a pane taller than its content is not scrolled');

console.log('comments pane: all assertions passed');
