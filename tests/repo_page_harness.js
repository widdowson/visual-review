// Runs static/repo.html's inline script for real, against a DOM and fetch shim.
//
// The structural check in test_repo_page.js reads that script as text and
// asserts call shapes. It cannot see which branch of fail() runs, and both
// findings against this page's failure handling have been branch findings: a
// message written where the next redraw erases it (round 2), and a failed
// first pass whose error a filter click replaced with "No open pull requests."
// (round 3). Reading the source could not have caught either. Running it does.
//
// The shim is the same one test_repo_page.js uses for renderList, plus enough
// of the page's surroundings to let the IIFE start: getElementById, a
// resolvable location, a localStorage that forgets, and a fetch that serves a
// queue of canned responses. `href` is modelled as a plain data property, as it
// is there and for the same reason.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const runfiles = process.env.RUNFILES_DIR || '';
const root = runfiles
  ? path.join(runfiles, '_main')
  : path.join(__dirname, '..');

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

// Every node under a root, so a check can ask what is on the page rather than
// where in the tree it ended up.
function descend(node) {
  return [node].concat(...node.children.map(descend));
}

// Drives the page once. `responses` is served to successive fetch calls in
// order; the page makes two (probe=0 then probe=1). `pathname` defaults to a
// well-formed repo URL. Resolves once both loads have settled.
function drivePage(responses, opts) {
  opts = opts || {};
  const html = fs.readFileSync(path.join(root, 'static', 'repo.html'), 'utf8');
  const open = html.lastIndexOf('<script>');
  const close = html.lastIndexOf('</script>');
  if (open < 0 || close < open) {
    throw new Error('static/repo.html must end with an inline <script> block');
  }
  const script = html.slice(open + '<script>'.length, close);

  const els = {
    'theme-toggle': element('button'),
    'pr-list': element('div'),
    'summary': element('span'),
    'hide-empty': element('input'),
    'repo-link': element('a'),
  };
  els['hide-empty'].checked = false;

  let call = 0;
  const served = [];
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
      if (body instanceof Error) return Promise.reject(body);
      return Promise.resolve({ json: () => Promise.resolve(body) });
    },
    // repo.js is required into the outer realm, so its `document` is the
    // outer global's; point that at the same shim.
    VRRepo: require(path.join(root, 'static', 'repo.js')),
    Date, JSON, String, Number, Boolean, Object, Array, Promise,
    isNaN, Math, console,
  };
  ctx.globalThis = ctx;
  global.document = ctx.document;
  vm.createContext(ctx);
  vm.runInContext(script, ctx);

  const handle = {
    get summary() { return els['summary'].textContent; },
    get summaryClass() { return els['summary'].className; },
    get listText() { return els['pr-list'].textContent; },
    get nodes() { return descend(els['pr-list']); },
    classed(cls) {
      return handle.nodes.filter(n => String(n.className).split(/\s+/).includes(cls));
    },
    get badges() {
      return handle.nodes
        .filter(n => String(n.className).includes('pr-badge'))
        .map(n => n.textContent);
    },
    get requests() { return served.slice(); },
    toggleFilter() {
      els['hide-empty'].checked = !els['hide-empty'].checked;
      els['hide-empty'].fire('change');
      return handle;
    },
  };

  // The page chains two fetches; a few turns of the microtask queue settle
  // both. setImmediate rather than a timer so this stays deterministic.
  return new Promise(resolve => {
    let turns = 0;
    (function spin() {
      if (++turns > 20) return resolve(handle);
      setImmediate(spin);
    })();
  });
}

module.exports = { drivePage };
