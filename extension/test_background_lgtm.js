// The LGTM half of background.js: what it will ask Visual Review, and on whose
// behalf. Runs the real region extracted from background.js (between
// `// vr:lgtm:begin` and `// vr:lgtm:end`) with a stub fetch, so a copy of the
// logic here cannot drift from the shipped one.

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const runfiles = process.env.RUNFILES_DIR || '';
const BG_PATH = runfiles
  ? path.join(runfiles, '_main', 'extension', 'background.js')
  : path.join(__dirname, 'background.js');
const source = fs.readFileSync(BG_PATH, 'utf8');

const BEGIN = '// vr:lgtm:begin';
const END = '// vr:lgtm:end';
assert.strictEqual(source.split(BEGIN).length, 2, 'exactly one ' + BEGIN);
assert.strictEqual(source.split(END).length, 2, 'exactly one ' + END);
const body = source.slice(source.indexOf(BEGIN), source.indexOf(END));

function load(fetchStub) {
  // `fetch` is a parameter so it shadows the global inside the region.
  return new Function('fetch', body +
    '\nreturn { lgtmTarget, lgtmHandle, lgtmResult, lgtmTrustedSender };')(fetchStub);
}

const good = { type: 'lgtm-status', owner: 'widdowson', repo: 'apwphotos-appv2', prNumber: 1841 };

async function main() {
  const { lgtmTarget, lgtmTrustedSender } = load(() => { throw new Error('no fetch'); });

  // ── target validation ──
  assert.strictEqual(lgtmTarget(good),
    'https://vr.apw.photos/api/widdowson/apwphotos-appv2/pr/1841/lgtm');
  const bad = [
    null, 'x', {},
    Object.assign({}, good, { owner: '..' }),
    Object.assign({}, good, { repo: '.' }),
    Object.assign({}, good, { owner: 'a/b' }),
    Object.assign({}, good, { repo: 'r?x=1' }),
    Object.assign({}, good, { repo: 'r%2Fx' }),
    Object.assign({}, good, { owner: '' }),
    Object.assign({}, good, { prNumber: '1841' }),
    Object.assign({}, good, { prNumber: 1.5 }),
    Object.assign({}, good, { prNumber: 0 }),
    Object.assign({}, good, { prNumber: -3 }),
  ];
  for (const m of bad) assert.strictEqual(lgtmTarget(m), null, 'refused: ' + JSON.stringify(m));

  // ── who may ask ──
  const id = 'abcdef';
  assert.ok(lgtmTrustedSender({ id, url: 'https://github.com/o/r/pull/1' }, id));
  assert.ok(lgtmTrustedSender({ id, tab: { url: 'https://github.com/o/r/pull/1' } }, id));
  assert.ok(!lgtmTrustedSender({ id: 'other', url: 'https://github.com/o/r/pull/1' }, id));
  assert.ok(!lgtmTrustedSender({ id, url: 'https://github.com.evil.example/' }, id));
  assert.ok(!lgtmTrustedSender({ id, url: 'https://evil.example/https://github.com/' }, id));
  assert.ok(!lgtmTrustedSender(null, id));

  // ── the requests it makes ──
  let seen = [];
  function stub(resp) {
    return (url, opts) => { seen.push({ url, opts }); return Promise.resolve(resp); };
  }
  const json = (status, body) => ({ type: 'cors', status, ok: status < 300, json: () => Promise.resolve(body) });

  let h = load(stub(json(200, { can_apply: true, applied: false })));
  let r = await h.lgtmHandle(good);
  assert.strictEqual(seen.length, 1);
  assert.strictEqual(seen[0].opts.method, 'GET');
  assert.strictEqual(seen[0].opts.credentials, 'include');
  assert.strictEqual(seen[0].opts.redirect, 'manual');
  assert.ok(!seen[0].opts.headers['X-VR-Intent'], 'a status read carries no intent');
  assert.deepStrictEqual(r.body, { can_apply: true, applied: false });
  assert.strictEqual(r.page, 'https://vr.apw.photos/widdowson/apwphotos-appv2/pr/1841');

  seen = [];
  h = load(stub(json(200, { ok: true, applied: true })));
  r = await h.lgtmHandle(Object.assign({}, good, { type: 'lgtm-apply' }));
  assert.strictEqual(seen[0].opts.method, 'POST');
  assert.strictEqual(seen[0].opts.headers['X-VR-Intent'], 'lgtm');
  assert.strictEqual(seen[0].opts.credentials, 'include');
  assert.strictEqual(r.ok, true);

  // Access redirecting to its login page means "sign in", not "refused".
  h = load(stub({ type: 'opaqueredirect', status: 0, ok: false }));
  r = await h.lgtmHandle(good);
  assert.strictEqual(r.signIn, true);

  // A refusal comes back as the server's body, not a sign-in.
  h = load(stub(json(403, { error: 'x@y may not apply LGTM' })));
  r = await h.lgtmHandle(Object.assign({}, good, { type: 'lgtm-apply' }));
  assert.strictEqual(r.status, 403);
  assert.ok(!r.signIn);
  assert.strictEqual(r.body.error, 'x@y may not apply LGTM');

  // Bad input and unknown types never fetch.
  seen = [];
  h = load(stub(json(200, {})));
  assert.ok((await h.lgtmHandle(Object.assign({}, good, { owner: '..' }))).error);
  assert.ok((await h.lgtmHandle(Object.assign({}, good, { type: 'lgtm-remove' }))).error);
  assert.strictEqual(seen.length, 0);

  // A network failure is reported, with the page to open.
  h = load(() => Promise.reject(new Error('offline')));
  r = await h.lgtmHandle(good);
  assert.strictEqual(r.error, 'offline');
  assert.ok(r.page);

  console.log('test_background_lgtm: all checks passed');
}

main().catch((e) => { console.error(e); process.exit(1); });
