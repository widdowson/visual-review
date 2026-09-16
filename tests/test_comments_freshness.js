// Exercises the stale-response guard on the comments pane (issue #36).
//
// The pane is shared by every file, so a comments fetch that lands after the
// user has moved on could render one file's comments under another's image.
// The decision of whether a response may still render is a pure function in
// static/index.html and is extracted and run here; the fetch around it touches
// the DOM and the network, so what that side gets is structural checks on its
// real source. Both halves are needed: the predicate is the same bug if it is
// never called, or called with the wrong things.

const assert = require('assert');
const { extract, bodyOf } = require('./spa_source');

const commentsResponseIsCurrent = extract('comments-freshness', 'commentsResponseIsCurrent');

// A response from the load that owns the pane, with no newer load started
// since it went out.
function fresh(over) {
  return Object.assign({
    ownedPane: true,
    generation: 3,
    currentGeneration: 3,
  }, over || {});
}

assert.strictEqual(commentsResponseIsCurrent(fresh()), true,
  'the response the pane is waiting for must render');

// The reported bug: j to file B while A's request is still open. Selecting B
// started B's own load, which took generation 4, so A's answer comes back
// holding a number that is no longer current and must not take the pane B's
// image is in. The same comparison covers two answers for the *same* file --
// posting a comment reloads it while the initial load is still open, and the
// older answer must not land last.
assert.strictEqual(
  commentsResponseIsCurrent(fresh({ generation: 3, currentGeneration: 4 })),
  false,
  'a response holding a superseded generation must not render');

// A reload that never owned the pane. Posting a comment on A reloads A by
// path, and that reload can fire after the user has moved to B. It takes no
// generation number -- taking one would strand B's own load -- so it carries
// the current one and the generation check alone would let it through.
assert.strictEqual(
  commentsResponseIsCurrent(fresh({ ownedPane: false })),
  false,
  'a load that did not own the pane must never render, even holding the current generation');

// Guarded against the loose spellings: a truthy non-boolean must not pass, and
// an absent flag must not either.
for (const value of [1, 'yes', {}, undefined, null]) {
  assert.strictEqual(commentsResponseIsCurrent(fresh({ ownedPane: value })), false,
    'ownedPane must be strictly true to render, got ' + String(value));
}

// Nor may a missing generation read as a match against a real one.
assert.strictEqual(
  commentsResponseIsCurrent(fresh({ generation: undefined, currentGeneration: 3 })),
  false, 'a response with no generation must not render');

// -- The SPA must use it, with the right inputs ------------------------------
// Every case above passes explicit arguments, so all of them would still pass
// with the guard absent from static/index.html, or called with a constant. The
// code that calls it touches the DOM and the network and so cannot be run
// here; these are checks on its source.

const loadComments = bodyOf('loadComments');

assert.ok(/commentsResponseIsCurrent\s*\(/.test(loadComments),
  'loadComments must consult commentsResponseIsCurrent');

// Each input matched with the expression it must carry, not the bare key: the
// point of every one of them is *which* value it reads, and a bare-key check
// passes on `ownedPane: true`, which is the bug.
const inputs = [
  ['ownedPane', 'ownedPane'],
  ['generation', 'generation'],
  ['currentGeneration', 'commentsGeneration'],
];
for (const [key, value] of inputs) {
  const expr = value.replace(/[.()[\]]/g, (c) => '\\s*\\' + c + '\\s*');
  assert.ok(new RegExp(key + '\\s*:\\s*' + expr + '\\s*,').test(loadComments),
    'loadComments must pass ' + value + ' as ' + key);
}

// The counter is what orders two loads, so it has to move on every load that
// owns the pane -- and must not move on one that does not, or the load that
// does own the pane is stranded behind a generation it can never match and its
// spinner stays up for good.
assert.ok(/ownedPane\s*\?\s*\+\+\s*commentsGeneration\s*:\s*commentsGeneration/.test(loadComments),
  'loadComments must take a new generation only when it owns the pane');
assert.ok(/ownedPane\s*=\s*path\s*===\s*state\.currentFile/.test(loadComments),
  'loadComments must decide ownedPane by comparing its path with the selected file');

// Both arrival paths are guarded. The catch branch renders too -- an empty
// pane written over the wrong file is the same defect as a full one.
const renders = loadComments.split(/renderComments\s*\(/);
assert.strictEqual(renders.length, 3,
  'loadComments must call renderComments exactly twice (the response and the failure)');
for (let i = 1; i < renders.length; i++) {
  assert.ok(/if\s*\(\s*!\s*isCurrent\s*\(\s*\)\s*\)\s*return\s*;$/.test(renders[i - 1].trimEnd()),
    'every renderComments call in loadComments must be immediately preceded by the freshness guard');
}

// The badge is keyed on the response's own path, so it is right for whatever
// file the response is about and is deliberately updated before the guard.
// Moving it below would silently lose the count for a comment the user posted
// and then navigated away from.
const badgeAt = loadComments.indexOf('updateCommentBadge');
const guardAt = loadComments.indexOf('if (!isCurrent()) return;');
assert.ok(badgeAt > 0, 'loadComments must still update the sidebar badge');
assert.ok(guardAt > 0, 'loadComments must guard on isCurrent()');
assert.ok(badgeAt < guardAt,
  'updateCommentBadge must run before the freshness guard: its row is chosen by the ' +
  "response's own path, so it is correct for a file that is no longer on screen");

// The spinner is written only by the load that owns the pane. Writing it from
// a reload the user has navigated away from would blank the pane for the file
// they are actually looking at, which is the same defect with a different
// payload.
const spinnerAt = loadComments.indexOf('spinner');
const ownedAt = loadComments.indexOf('if (ownedPane) {');
assert.ok(spinnerAt > 0, 'loadComments must still show a spinner while it loads');
assert.ok(ownedAt > 0 && ownedAt < spinnerAt,
  'the spinner must be written only when this load owns the pane');

console.log('comments freshness: all assertions passed');
