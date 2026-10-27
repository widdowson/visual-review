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
// Anchored on the statement's end, and pinned as assigned exactly once. An
// unanchored form let `|| true` be appended to the comparison, and a check on
// the assignment alone let a second one two lines later overwrite it; either
// makes ownedPane unconditionally true and hands every stale response the
// pane, which is issue #36 in full rather than a variant. Both are sabotage
// rather than slips, but they are two lines to close.
assert.ok(/ownedPane\s*=\s*\(?\s*path\s*===\s*state\s*\.\s*currentFile\s*\)?\s*;/.test(loadComments),
  'loadComments must decide ownedPane by comparing its path with the selected file, ' +
  'and by nothing else');
assert.strictEqual((loadComments.match(/\bownedPane\s*=[^=]/g) || []).length, 1,
  'ownedPane must be assigned exactly once: a later reassignment would make every ' +
  'stale response look like the one the pane is waiting for');

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
// and then navigated away from. It goes through writeCommentBadge, which
// orders writes per row (#49); the run further down holds that to behaviour.
const badgeAt = loadComments.indexOf('writeCommentBadge');
const guardAt = loadComments.search(/if\s*\(\s*!\s*isCurrent\s*\(\s*\)\s*\)\s*return\s*;/);
assert.ok(badgeAt > 0, 'loadComments must still update the sidebar badge');
assert.ok(guardAt > 0, 'loadComments must guard on isCurrent()');
assert.ok(badgeAt < guardAt,
  'writeCommentBadge must run before the freshness guard: its row is chosen by the ' +
  "response's own path, so it is correct for a file that is no longer on screen");

// The spinner is written only by the load that owns the pane. Writing it from
// a reload the user has navigated away from would blank the pane for the file
// they are actually looking at, which is the same defect with a different
// payload.
//
// Two earlier versions of this assertion let the defect it names run green,
// and each failed for a different reason worth not repeating.
//
// The first compared the index of `if (ownedPane) {` against the index of
// `spinner`: position, not containment, so hoisting only the innerHTML write
// out of the block left the literal in place and still ahead of it.
//
// The second walked the block's braces and looked inside -- which closed that
// one, but asserted on the *markup token* rather than on the write. Keep the
// markup inside the block, assign it to a variable and write that variable
// outside, and the counts are unchanged while a non-owning reload writes ''
// over the pane and blanks the file the user is looking at. That walk was also
// a second naive brace walk in a repo whose spa_source.js documents at length
// why the first one needed a parse check.
//
// The third asserted on the write and not the markup, which closed that one
// -- but spelled the write out as `.innerHTML =`, so it enumerated a mechanism
// rather than a property. An alias, `textContent`, `replaceChildren`,
// `insertAdjacentHTML`, bracket notation and `+=` each blank the pane past it,
// and of those `+=` is a slip a maintainer could make while appending a notice.
//
// So this counts *references to the element* instead. It cannot be told apart
// by spelling, there is no next mechanism to enumerate, and it is a shorter
// regex than the one it replaces -- which is the point: the fix for each of
// these has to remove specificity, or the next round finds the next spelling.
//
// The reach is what `bodyOf` can see: a write from a helper defined elsewhere
// is outside it. tests/spa_source.js states that trade as the project's
// position and it is not chased here -- the instrument for it is a browser
// test, which does not care where the write lives, and is issue #53.
// The block is matched whole, opener through closing brace, with `[^{}]*` for
// its contents -- a regex rather than a brace walk, so it cannot over-capture
// past the block the way a walk can when a `{` and a `}` are split across two
// strings. A nested brace inside the block fails this loudly rather than
// quietly widening it, which is the way round spa_source.js argues for.
//
// Deliberately tolerant of everything that is not the property: spacing, extra
// parens, quote style, and the order of the statements inside.
const ownedBlock = /if\s*\(+\s*ownedPane\s*\)+\s*\{([^{}]*)\}/.exec(loadComments);
assert.ok(ownedBlock, 'loadComments must guard the pane write with an ownedPane block');
const guarded = ownedBlock[1];

// Every mention of either element must be inside that block. Counting mentions
// rather than writes is what makes this mechanism-blind; comparing the count
// inside against the count in the whole body is what keeps it from punishing a
// statement *added* inside the block, which is guarded by construction.
const refs = (source, name) => (source.match(new RegExp('\\b' + name + '\\b', 'g')) || []).length;
for (const name of ['commentsScroll', 'commentsSection']) {
  assert.ok(refs(guarded, name) > 0, name + ' must be touched inside the ownedPane block');
  assert.strictEqual(refs(loadComments, name), refs(guarded, name),
    'loadComments must touch ' + name + ' only inside the ownedPane block: a write ' +
    'outside it, however spelled, is one a reload the user has navigated away from ' +
    'would reach, blanking the pane for the file they are actually looking at');
}

assert.ok(/commentsScroll\s*\.\s*innerHTML\s*=/.test(guarded),
  'the spinner write must be inside the ownedPane block');
// Requiring the markup itself, rather than merely "not the empty string", is
// the one place this stays literal on purpose: the alternative is enumerating
// the ways of spelling an empty write, which is the trap the reference count
// above exists to avoid. The cost is that hoisting the markup to a named
// constant outside the block fails here with nothing wrong. That is a loud
// failure naming what the pattern wants, which spa_source.js argues is the
// right way round -- move the constant inside the block, or teach this line.
assert.ok(/spinner/.test(guarded),
  'the ownedPane block must write the spinner markup, not blank the pane');
assert.ok(/commentsSection\s*\.\s*style\s*\.\s*display\s*=/.test(guarded),
  'revealing the pane belongs inside the ownedPane block too');

// ── The badge, per file (#49) ───────────────────────────────────────────────
//
// The pane's guard orders loads of the pane; the badge is per file and needs
// its own order. The predicate first, then the real loadComments,
// loadCommentCounts and writeCommentBadge run against stubs, because the bug
// is an interleaving of responses and only a run can produce one.

const commentBadgeWriteIsNewer = extract('comment-badge', 'commentBadgeWriteIsNewer');

assert.strictEqual(commentBadgeWriteIsNewer(1, undefined), true,
  'the first write to a row must apply');
assert.strictEqual(commentBadgeWriteIsNewer(5, 4), true, 'a later-started write must apply');
assert.strictEqual(commentBadgeWriteIsNewer(4, 5), false,
  'an earlier-started write landing late must not replace a later one');
assert.strictEqual(commentBadgeWriteIsNewer(5, 5), false,
  'nor may one write apply twice');
for (const value of [undefined, null, '6', NaN]) {
  assert.strictEqual(commentBadgeWriteIsNewer(value, 4), false,
    'an unnumbered write must be refused, got ' + String(value));
  assert.strictEqual(commentBadgeWriteIsNewer(value, undefined), false,
    'even on a row nothing has written to, got ' + String(value));
}

// The harness. Every fetch is held until the case resolves it, so a case can
// land responses in whatever order the bug needs. The declarations the three
// functions close over are lifted from the SPA's own source rather than
// re-declared here, so their starting values are the shipped ones too.
function harness(currentFile) {
  const script = require('./spa_source').scriptSource();
  const decl = name => {
    const m = new RegExp('var\\s+' + name + '\\s*=[^;]*;').exec(script);
    assert.ok(m, 'static/index.html must declare ' + name);
    return m[0];
  };
  const pending = [];
  const badges = {};
  const urls = [];
  const fetch = url => new Promise(resolve => {
    urls.push(url);
    pending.push({ url, answer: data => resolve({ json: () => data }) });
  });
  const el = () => ({ style: {}, innerHTML: '' });
  const clock = { now: 1000000 };
  const make = new Function(
    'state', 'commentsSection', 'commentsScroll', 'apiBase', 'fetch', 'renderComments',
    'commentsResponseIsCurrent', 'commentBadgeWriteIsNewer', 'updateCommentBadge', 'Date',
    [decl('commentBadgeSequence'), decl('commentBadgeApplied'), decl('commentsGeneration'),
      decl('COMMENTS_FRESH_WINDOW_MS'), decl('commentsFreshUntil'),
      bodyOf('writeCommentBadge'), bodyOf('loadComments'), bodyOf('loadCommentCounts'),
      // What the post handler does on success, taken from its own source
      // below rather than restated here.
      'function posted() { ' + postedStatement + ' }',
      'return { loadComments: loadComments, loadCommentCounts: loadCommentCounts, ' +
      'posted: posted, window: COMMENTS_FRESH_WINDOW_MS };'].join('\n'));
  const fns = make({ currentFile }, el(), el(), '/api', fetch, () => {},
    commentsResponseIsCurrent, commentBadgeWriteIsNewer,
    (path, count) => { badges[path] = count; }, { now: () => clock.now });
  const settle = () => new Promise(r => setImmediate(r));
  return Object.assign(fns, { pending, badges, urls, settle, clock });
}

// The statement the post handler runs on success to open the fresh window,
// lifted from renderComments, and required to come before its reload so that
// the reload is inside the window.
const postBody = bodyOf('renderComments');
const postedMatch = /commentsFreshUntil\s*=\s*Date\s*\.\s*now\s*\(\s*\)\s*\+\s*COMMENTS_FRESH_WINDOW_MS\s*;/.exec(postBody);
assert.ok(postedMatch, 'a successful post must open the fresh window');
const postedStatement = postedMatch[0];
assert.ok(postBody.indexOf(postedStatement) < postBody.search(/loadComments\s*\(\s*path\s*\)/),
  'the window must open before the reload after a post, or that reload is outside it');

// The window has to cover the server's whole TTL: short of it, another
// instance's list from before the post can still be served once the window has
// closed. Covering the TTL is not quite a guarantee either — see the comment on
// COMMENTS_FRESH_WINDOW_MS for the one walk's duration it leaves.
{
  const fs = require('fs');
  const path = require('path');
  const runfiles = process.env.RUNFILES_DIR || '';
  const appPy = fs.readFileSync(runfiles
    ? path.join(runfiles, '_main', 'app.py')
    : path.join(__dirname, '..', 'app.py'), 'utf8');
  const ttl = /^_PR_COMMENTS_TTL\s*=\s*(\d+)\s*$/m.exec(appPy);
  assert.ok(ttl, 'app.py must define _PR_COMMENTS_TTL as a whole number of seconds');
  const windowMs = Number(/var\s+COMMENTS_FRESH_WINDOW_MS\s*=\s*(\d+)\s*;/
    .exec(require('./spa_source').scriptSource())[1]);
  assert.strictEqual(windowMs, Number(ttl[1]) * 1000,
    'COMMENTS_FRESH_WINDOW_MS must equal _PR_COMMENTS_TTL');
}

const page = n => ({ comments: Array.from({ length: n }, (_, i) => ({ id: i })) });

(async () => {
  // The issue's interleaving: the initial load is slow, a post reloads the
  // file and answers 5, then the initial load lands with 4. The badge must
  // stay at 5.
  {
    const h = harness('a.png');
    h.loadComments('a.png');
    h.loadComments('a.png');
    h.pending[1].answer(page(5));
    await h.settle();
    assert.strictEqual(h.badges['a.png'], 5);
    h.pending[0].answer(page(4));
    await h.settle();
    assert.strictEqual(h.badges['a.png'], 5,
      'a slow older answer must not put the badge back to the count before the post');
  }

  // In order, the later answer still wins, so ordering is by start and not by
  // a flag that only ever lets the first write through.
  {
    const h = harness('a.png');
    h.loadComments('a.png');
    h.pending[0].answer(page(4));
    await h.settle();
    h.loadComments('a.png');
    h.pending[1].answer(page(5));
    await h.settle();
    assert.strictEqual(h.badges['a.png'], 5, 'a newer answer must replace an older one');
  }

  // The order is per row. A load for b.png started after a.png's must not
  // block a.png's answer, which a single counter shared by every row would.
  {
    const h = harness('b.png');
    h.loadComments('a.png');
    h.loadComments('b.png');
    h.pending[1].answer(page(2));
    h.pending[0].answer(page(3));
    await h.settle();
    assert.deepStrictEqual(h.badges, { 'a.png': 3, 'b.png': 2 },
      'each row must take its own newest answer');
  }

  // The page-load tally is a badge write too. A comment posted while it is in
  // flight produces a newer count, and the tally landing after must not
  // overwrite it — but must still fill every row the load did not reach.
  {
    const h = harness('a.png');
    h.loadCommentCounts();
    h.loadComments('a.png');
    h.pending[1].answer(page(5));
    await h.settle();
    h.pending[0].answer({ counts: { 'a.png': 4, 'b.png': 7 } });
    await h.settle();
    assert.deepStrictEqual(h.badges, { 'a.png': 5, 'b.png': 7 },
      'the tally must not overwrite a newer per-file count, and must fill the rest');
  }

  // Loads ask the server to skip its cache for one TTL after a post, and only
  // then. Not only the reload: clicking away and back inside the window can
  // reach an instance holding the pre-post list, which would take the comment
  // out of the pane and the badge back to the old count (round 1, Minor 1).
  // Outside the window loads may be answered from the cache, which is the
  // point of having one (#50).
  {
    const h = harness('a.png');
    h.loadComments('a.png');
    h.posted();
    h.loadComments('a.png');
    h.loadComments('b.png');
    h.clock.now += h.window - 1;
    h.loadComments('a.png');
    h.clock.now += 1;
    h.loadComments('a.png');
    assert.deepStrictEqual(h.urls, [
      '/api/comments?path=a.png',
      '/api/comments?path=a.png&fresh=1',
      '/api/comments?path=b.png&fresh=1',
      '/api/comments?path=a.png&fresh=1',
      '/api/comments?path=a.png',
    ]);
  }

  // The tally takes a sequence number of its own. Sharing the last load's
  // would tie with it, and a tie is refused, so a tally started after that
  // load and holding a newer count would leave the older one showing.
  {
    const h = harness('a.png');
    h.loadComments('a.png');
    h.pending[0].answer(page(4));
    await h.settle();
    h.loadCommentCounts();
    h.pending[1].answer({ counts: { 'a.png': 5 } });
    await h.settle();
    assert.strictEqual(h.badges['a.png'], 5,
      'a tally started after a load must replace that load\'s count');
  }

  console.log('comments freshness: all assertions passed');
})().catch(err => { console.error(err); process.exit(1); });
