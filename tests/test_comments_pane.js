// Exercises the comments-pane arithmetic that lives in static/index.html.
//
// The SPA is one file by design, so the two functions are extracted from it
// here rather than copied: a test holding its own copy of the logic would keep
// passing after the real one changed. The markers are part of the contract —
// if they go missing this test fails rather than silently testing nothing.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { extract, bodyOf } = require('./spa_source');

const computeCommentsPaneHeight = extract('comments-pane', 'computeCommentsPaneHeight');
const computeCommentsScrollTop = extract('comments-pane', 'computeCommentsScrollTop');

// ── The shipped tuning ──────────────────────────────────────────────────────
// Asserted because every case below passes explicit arguments, so all of them
// would still pass with the feature switched off in the SPA. Each assertion is
// a floor on the feature existing, not an opinion about its tuning: raising
// either number is a judgement call and must not fail here.

const tuning = extract('comments-pane', 'COMMENTS_PANE_TUNING');
assert.strictEqual(typeof tuning, 'object', 'the region must export a COMMENTS_PANE_TUNING object');
assert.deepStrictEqual(
  Object.keys(tuning).sort(), ['minPaneHeight', 'minViewportHeight'],
  'COMMENTS_PANE_TUNING gained or lost a key; the assertions below need updating');
for (const [k, v] of Object.entries(tuning)) {
  assert.ok(Number.isInteger(v) && v > 0,
    'COMMENTS_PANE_TUNING.' + k + ' must be a positive integer, got ' + v);
}
assert.ok(tuning.minViewportHeight > 0,
  'the image comparator must keep a reserve however far the pane is dragged');

// The SPA must actually feed the tuning to the function, and must use the
// scroll helper: the cases below all pass explicit arguments, so none of them
// would notice a hard-coded 0 or a `scrollTop = scrollHeight` left in place.
// Each knob is matched with the option it fills, not on its own — with bare
// tokens, `minPaneHeight: COMMENTS_PANE_TUNING.minViewportHeight` would keep
// both present and pass.
const resizeBody = bodyOf('resizeCommentsPane');
for (const key of ['minPaneHeight', 'minViewportHeight']) {
  assert.ok(
    new RegExp(key + '\\s*:\\s*COMMENTS_PANE_TUNING\\s*\\.\\s*' + key + '\\b').test(resizeBody),
    'resizeCommentsPane must pass COMMENTS_PANE_TUNING.' + key + ' as the clamp\'s ' + key);
}
assert.ok(/computeCommentsPaneHeight\s*\(/.test(resizeBody),
  'resizeCommentsPane must apply the clamp rather than setting a raw height');

const scrollBody = bodyOf('scrollToNewestComment');
assert.ok(/computeCommentsScrollTop\s*\(/.test(scrollBody),
  'scrollToNewestComment must use computeCommentsScrollTop');
assert.ok(!/scrollTop\s*=\s*\w+\.scrollHeight\b/.test(scrollBody),
  'scrolling to the very bottom lands on the comment form, not on the newest comment');

// ── The initial cap is CSS, so assert it is still there ─────────────────────
// computeCommentsPaneHeight governs the drag; the height the pane opens at is
// the `max-height` on .comments-section. Without this the initial cap could be
// deleted with every case below still green. Read from the file with comments
// stripped, so a commented-out rule cannot stand in for a live one.
const HTML_PATH = process.env.RUNFILES_DIR
  ? path.join(process.env.RUNFILES_DIR, '_main', 'static', 'index.html')
  : path.join(__dirname, '..', 'static', 'index.html');
const css = fs.readFileSync(HTML_PATH, 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, ' ');
// Every .comments-section rule, not just the first: the mobile media query
// carries a second one, and keying off whichever comes first would make a
// reorder of the stylesheet read as a deleted cap.
const paneRules = css.match(/\.comments-section\s*\{[^}]*\}/g) || [];
assert.ok(paneRules.length, 'static/index.html must style .comments-section');

// The cap has to apply unconditionally, so it is looked for among the rules
// that are not nested in an @media block — a cap that lives only in the mobile
// query leaves every desktop window uncapped, which is the bug this fixes.
// Depth is counted over the stylesheet rather than matched, since an @media
// block is a brace inside a brace and a regex cannot tell the two apart.
function unconditionalRules(stylesheet, selector) {
  const out = [];
  let depth = 0;
  let ruleStart = 0;
  for (let i = 0; i < stylesheet.length; i++) {
    if (stylesheet[i] === '{') {
      if (depth === 0) ruleStart = i;
      depth++;
    } else if (stylesheet[i] === '}') {
      depth--;
      // A rule whose own opening brace was at depth 0 is not inside @media.
      if (depth === 0 && stylesheet.slice(0, ruleStart).trimEnd().endsWith(selector)) {
        out.push(stylesheet.slice(ruleStart, i + 1));
      }
    }
  }
  return out;
}

const unconditionalPaneRules = unconditionalRules(css, '.comments-section');
assert.ok(unconditionalPaneRules.length,
  '.comments-section must be styled outside any @media block');
const caps = unconditionalPaneRules
  .map((rule) => rule.match(/max-height:\s*([\d.]+)vh/))
  .filter(Boolean);
assert.strictEqual(caps.length, 1,
  '.comments-section must cap its initial height in vh, unconditionally and ' +
  'in exactly one rule');
assert.ok(Number(caps[0][1]) > 0 && Number(caps[0][1]) <= 50,
  'the initial cap must leave the comparator the majority of the column, got ' +
  caps[0][1] + 'vh');
// The body must be the thing that scrolls: a pane that scrolls as a whole puts
// its drag handle over whatever comment it is scrolled against.
assert.ok(/\.comments-scroll\s*\{[^}]*overflow-y:\s*auto/.test(css),
  '.comments-scroll must be the scrolling element');
for (const rule of paneRules) {
  assert.ok(!/overflow-y:\s*auto/.test(rule),
    '.comments-section must not scroll itself; .comments-scroll does');
}

// ── The drag clamp ──────────────────────────────────────────────────────────
// A 900px window with the comparator starting 130px down, which is what the
// real page measures at 1440×900.
function height(overrides) {
  return computeCommentsPaneHeight(Object.assign({
    paneBottom: 900, viewportTop: 130, pointerY: 600,
    minPaneHeight: 60, minViewportHeight: 120,
  }, overrides));
}

// The ceiling those numbers imply: 900 - 130 - 120.
const CEILING = 650;

assert.strictEqual(height({}), 300, 'an unclamped drag is the distance dragged');
assert.strictEqual(height({ pointerY: 250 }), CEILING, 'a drag exactly to the ceiling reaches it');
assert.strictEqual(height({ pointerY: 249 }), CEILING, 'a drag past the ceiling stops at it');
assert.strictEqual(height({ pointerY: 0 }), CEILING, 'a drag to the top of the window stops at it');
assert.strictEqual(height({ pointerY: -500 }), CEILING, 'a pointer dragged off the top stops at it');
assert.strictEqual(height({ pointerY: 840 }), 60, 'a drag exactly to the floor reaches it');
assert.strictEqual(height({ pointerY: 890 }), 60, 'a drag past the floor stops at it');
assert.strictEqual(height({ pointerY: 2000 }), 60, 'a pointer dragged off the bottom stops at it');

// The point of the ceiling: the comparator keeps its reserve at every pointer
// position, which is what a fraction of the window did not do — 80% of a 900px
// window is 720px, leaving the comparator 46px of the 770px column.
for (let pointerY = -200; pointerY <= 1100; pointerY += 7) {
  const h = height({ pointerY });
  assert.ok(h >= 60, 'the pane never goes below its floor, at pointerY ' + pointerY);
  assert.ok(900 - 130 - h >= 120,
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
// clamp inverting and pinning the pane to a negative height.
assert.strictEqual(
  height({ paneBottom: 200, viewportTop: 100, pointerY: 0 }), 60,
  'a column shorter than both floors still yields a pane at its minimum');
assert.strictEqual(
  height({ paneBottom: 200, viewportTop: 100, pointerY: 199 }), 60,
  'and a drag to the bottom of that column yields the same');

// The tuning is read from the arguments, not baked in.
assert.strictEqual(height({ minPaneHeight: 200, pointerY: 890 }), 200,
  'the floor comes from minPaneHeight');
assert.strictEqual(height({ minViewportHeight: 400, pointerY: 0 }), 370,
  'the ceiling comes from minViewportHeight');

// ── The initial scroll position ─────────────────────────────────────────────
// Measured from the real page at 1440×900 on apwphotos-appv2 PR #209's
// cuj_proofing_05_quota_progress.png, which has six comments: a 269px pane
// over 860px of content, of which the post-a-comment form is the last 119px.
function scrollTop(overrides) {
  return computeCommentsScrollTop(Object.assign({
    lastCommentBottom: 741, clientHeight: 269, scrollHeight: 860,
  }, overrides));
}

assert.strictEqual(scrollTop({}), 472, 'the newest comment ends at the bottom of the pane');
assert.ok(scrollTop({}) < 860 - 269,
  'the pane must not be scrolled to its very bottom, which is the comment form');

assert.strictEqual(scrollTop({ lastCommentBottom: 0 }), 0,
  'a file with no comments shows the top of the pane');
assert.strictEqual(
  computeCommentsScrollTop({ lastCommentBottom: 150, clientHeight: 269, scrollHeight: 260 }), 0,
  'content shorter than the pane does not scroll');
assert.strictEqual(scrollTop({ lastCommentBottom: 200 }), 0,
  'a newest comment that already fits above the fold does not scroll');
assert.strictEqual(scrollTop({ lastCommentBottom: 269 }), 0,
  'a newest comment ending exactly at the fold does not scroll');
assert.strictEqual(scrollTop({ lastCommentBottom: 270 }), 1,
  'one pixel past the fold scrolls one pixel');
assert.strictEqual(scrollTop({ lastCommentBottom: 860 }), 591,
  'a newest comment ending at the content bottom scrolls all the way');
assert.strictEqual(scrollTop({ lastCommentBottom: 5000 }), 591,
  'a bottom past the content clamps to the last scrollable pixel');

// A pane dragged tall enough to hold everything shows everything from the top.
assert.strictEqual(scrollTop({ clientHeight: 900 }), 0,
  'a pane taller than its content is not scrolled');

console.log('comments pane: all assertions passed');
