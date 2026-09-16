// Runs static/repo.html's inline script for real, against a DOM and fetch shim.
//
// The structural check in test_repo_page.js reads that script as text and
// asserts call shapes. It cannot see which branch runs, and every finding
// against this page's failure handling has been a branch finding: a message
// written where the next redraw erased it, a failed first load whose error a
// filter click replaced with "No open pull requests.", and an error string the
// page never recognised as one. Reading the source could not have caught any of
// them. Running it does.
//
// static/repo.js is evaluated *inside* the same context rather than required
// from here, so it takes its own UMD browser branch and resolves `document` to
// this shim the way it does in a page. That is both more faithful and the
// reason nothing here has to touch the outer realm's globals — an earlier
// version assigned `global.document` and left it assigned, so every later
// assertion in test_repo_page.js rendered through this file's shim instead of
// the one documented beside it.
//
// The DOM shim is a copy of the one test_repo_page.js uses for renderList, and
// has to keep agreeing with it, plus enough of the page's surroundings to let
// the IIFE start: getElementById, a resolvable location, a localStorage that
// forgets, and a fetch serving canned responses. `href` is modelled as a plain
// data property, as it is there and for the same reason.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const runfiles = process.env.RUNFILES_DIR || '';
const root = runfiles ? path.join(runfiles, '_main') : path.join(__dirname, '..');

function element(tag) {
  return {
    tag: tag,
    className: '',
    children: [],
    _text: '',
    dataset: {},
    listeners: {},
    get textContent() {
      return this._text + this.children.map(c => c.textContent).join('');
    },
    set textContent(v) {
      this._text = String(v);
      this.children = [];
    },
    appendChild(child) { this.children.push(child); return child; },
    addEventListener(event, fn) {
      (this.listeners[event] = this.listeners[event] || []).push(fn);
    },
    fire(event) { (this.listeners[event] || []).forEach(fn => fn()); },
  };
}

function descend(node) {
  return [node].concat(...node.children.map(descend));
}

// The page chains two fetches; a few turns of the macrotask queue settle both.
// setImmediate rather than a timer, so this stays deterministic. The budget is
// a margin, not a completion signal: every scenario carries a positive
// assertion, so a page that has not finished fails rather than passes.
function settle(value) {
  return new Promise(resolve => {
    let turns = 0;
    (function spin() {
      if (++turns > 20) return resolve(value);
      setImmediate(spin);
    })();
  });
}

// Drives the page once. `responses` is served to successive fetch calls in
// order; the page makes two (probe=0 then probe=1). An Error is rejected
// rather than returned. Options:
//   pathname        the URL the page reads itself from (default '/o/r')
//   initialSummary  what #summary already says (default 'Loading…', which is
//                   what the served markup carries)
//   defer           hold every response until handle.release()
// Resolves once the loads have settled, or — with `defer` — while the first
// request is still in flight.
function drivePage(responses, opts) {
  opts = opts || {};
  const html = fs.readFileSync(path.join(root, 'static', 'repo.html'), 'utf8');
  const open = html.lastIndexOf('<script>');
  const close = html.lastIndexOf('</script>');
  if (open < 0 || close < open) {
    throw new Error('static/repo.html must end with an inline <script> block');
  }
  const pageScript = html.slice(open + '<script>'.length, close);
  const moduleScript = fs.readFileSync(path.join(root, 'static', 'repo.js'), 'utf8');

  const els = {
    'theme-toggle': element('button'),
    'pr-list': element('div'),
    'summary': element('span'),
    'hide-empty': element('input'),
    'repo-link': element('a'),
  };
  els['hide-empty'].checked = false;
  // The served document is not blank: static/repo.html ships #summary reading
  // "Loading…". Seeding it is what lets a test see the page before the first
  // answer arrives — a state the page really has, and one a click reaches.
  els['summary'].textContent =
    opts.initialSummary === undefined ? 'Loading…' : opts.initialSummary;

  let call = 0;
  const served = [];
  const pending = [];

  const ctx = {
    document: {
      createElement: element,
      getElementById: id => els[id],
      documentElement: { dataset: {} },
      title: '',
    },
    window: { matchMedia: () => ({ matches: false }) },
    location: { pathname: opts.pathname || '/o/r' },
    localStorage: { getItem: () => null, setItem: () => {} },
    fetch: url => {
      served.push(url);
      const body = responses[Math.min(call++, responses.length - 1)];
      const answer = () => body instanceof Error
        ? Promise.reject(body)
        : Promise.resolve({ json: () => Promise.resolve(body) });
      // `defer` holds every response until release(), so a test can look at
      // the page — and click it — while a request is still in flight. Without
      // it the harness can only ever see a settled page, which is how a click
      // during the first load went unexamined.
      if (!opts.defer) return answer();
      return new Promise(resolve => pending.push(() => resolve(answer())));
    },
    Date, JSON, String, Number, Boolean, Object, Array, Promise,
    isNaN, Math, console,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  // repo.js first: with no `module` in this context it takes its browser
  // branch and defines ctx.VRRepo, which the page then uses.
  vm.runInContext(moduleScript, ctx);
  if (!ctx.VRRepo) throw new Error('static/repo.js did not define VRRepo');
  vm.runInContext(pageScript, ctx);

  const handle = {
    get summary() { return els['summary'].textContent; },
    get summaryClass() { return els['summary'].className; },
    get listText() { return els['pr-list'].textContent; },
    get nodes() { return descend(els['pr-list']); },
    get badges() {
      return handle.nodes
        .filter(n => String(n.className).includes('pr-badge'))
        .map(n => n.textContent);
    },
    // The URLs the page asked for, in order.
    get requests() { return served.slice(); },
    toggleFilter() {
      els['hide-empty'].checked = !els['hide-empty'].checked;
      els['hide-empty'].fire('change');
      return handle;
    },
    // For a `defer` run: let every held response through and settle again.
    release() { pending.splice(0).forEach(fn => fn()); return settle(handle); },
  };

  return settle(handle);
}

module.exports = { drivePage };
