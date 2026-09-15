// Shared helper for the tests that exercise logic living inline in
// static/index.html. The SPA is one file by design, so these tests extract
// the real source from it rather than keeping a copy — a test holding its
// own copy of the logic keeps passing after the real one changes.

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const runfiles = process.env.RUNFILES_DIR || '';
const HTML_PATH = runfiles
  ? path.join(runfiles, '_main', 'static', 'index.html')
  : path.join(__dirname, '..', 'static', 'index.html');

const html = fs.readFileSync(HTML_PATH, 'utf8');

// Comments are stripped before anything is matched. The prose around the code
// is allowed to use words the purity check forbids, and — the reason this is
// shared rather than local to extract() — a commented-out previous value of a
// constant would otherwise mask the live one.
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

const htmlWithoutComments = stripComments(html);

// Anything that would make the extracted region depend on a browser, the
// network, or the SPA's mutable state. Checked against the region with
// comments stripped, since the prose around the code may use these words.
const IMPURE = [
  [/\bdocument\b/, 'document'],
  [/\bwindow\b/, 'window'],
  [/\blocation\b/, 'location'],
  [/\blocalStorage\b/, 'localStorage'],
  [/\bconsole\b/, 'console'],
  [/\brequire\b/, 'require'],
  [/\bnew\s+Image\b/, 'new Image'],
  [/\bfetch\s*\(/, 'fetch('],
  [/\bstate\s*[.[]/, 'state'],
  [/\bsetTimeout\b/, 'setTimeout'],
  [/\bnew\s+Date\b/, 'new Date'],
  [/\bDate\s*\.\s*now\b/, 'Date.now'],
  [/\bMath\s*\.\s*random\b/, 'Math.random'],
];

// Pull out the region between `// ── <name>:begin` and `// ── <name>:end`,
// check it is pure, and evaluate it in strict mode so an accidental implicit
// global throws here rather than quietly working against the SPA's sloppy
// IIFE.
function extract(name, exportName) {
  const begin = html.indexOf(name + ':begin');
  const end = html.indexOf(name + ':end');
  assert.ok(begin > 0, 'static/index.html must contain the ' + name + ':begin marker');
  assert.ok(end > begin, 'static/index.html must contain ' + name + ':end after ' + name + ':begin');

  const source = html.slice(html.indexOf('\n', begin) + 1, html.lastIndexOf('\n', end));
  const declaration = new RegExp('function\\s+' + exportName + '\\s*\\(');
  assert.ok(declaration.test(source),
    'the ' + name + ' region must define ' + exportName);

  const code = stripComments(source);
  for (const [pattern, label] of IMPURE) {
    assert.ok(!pattern.test(code),
      'the ' + name + ' region must stay pure; found "' + label + '" in it');
  }

  return new Function("'use strict';\n" + source + '\nreturn ' + exportName + ';')();
}

// The SPA's tuning constants, read off the source so a test can assert what
// the shipped defaults actually are. Matched against the comment-stripped
// source and required to be unique: a leftover `// var PREFETCH_AHEAD = 1;`
// above a live `var PREFETCH_AHEAD = 0;` would otherwise be read instead of
// the value that ships, which is exactly the mutant these assertions exist
// to catch.
function constant(name) {
  const pattern = new RegExp('\\bvar\\s+' + name + '\\s*=\\s*(-?\\d+)\\s*;', 'g');
  const matches = [...htmlWithoutComments.matchAll(pattern)];
  assert.strictEqual(matches.length, 1,
    'static/index.html must declare a numeric ' + name + ' exactly once, found ' +
    matches.length);
  return parseInt(matches[0][1], 10);
}

// The source of a top-level function in the SPA's inline script, from its
// `function` keyword to its matching close brace. Used for structural checks
// on code that touches the DOM and so cannot be extracted and run.
//
// Brace-matched rather than indentation-matched: an earlier version looked for
// a `}` at a fixed indent, so reindenting a function made the capture run on
// into the next one and a deleted call was then satisfied by the following
// function's own declaration line — a green run over a real defect.
function bodyOf(name) {
  const at = htmlWithoutComments.indexOf('function ' + name + '(');
  assert.ok(at >= 0, 'static/index.html must define ' + name);
  assert.strictEqual(
    htmlWithoutComments.indexOf('function ' + name + '(', at + 1), -1,
    name + ' must be defined exactly once');

  const open = htmlWithoutComments.indexOf('{', at);
  assert.ok(open > at, name + ' must have a body');
  let depth = 0;
  for (let i = open; i < htmlWithoutComments.length; i++) {
    const c = htmlWithoutComments[i];
    if (c === '{') depth++;
    else if (c === '}' && --depth === 0) return htmlWithoutComments.slice(at, i + 1);
  }
  assert.fail(name + ' has no matching close brace');
}

module.exports = { extract, constant, bodyOf, stripComments, html };
