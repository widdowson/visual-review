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

  const code = source
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
  for (const [pattern, label] of IMPURE) {
    assert.ok(!pattern.test(code),
      'the ' + name + ' region must stay pure; found "' + label + '" in it');
  }

  return new Function("'use strict';\n" + source + '\nreturn ' + exportName + ';')();
}

// The SPA's tuning constants, read off the source so a test can assert what
// the shipped defaults actually are.
function constant(name) {
  const m = html.match(new RegExp('\\bvar\\s+' + name + '\\s*=\\s*([-0-9]+)\\s*;'));
  assert.ok(m, 'static/index.html must declare a numeric ' + name);
  return parseInt(m[1], 10);
}

module.exports = { extract, constant, html };
