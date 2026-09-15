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
  const declaration = new RegExp('\\b(?:function|var|let|const)\\s+' + exportName + '\\b');
  assert.ok(declaration.test(source),
    'the ' + name + ' region must define ' + exportName);

  const code = stripComments(source);
  for (const [pattern, label] of IMPURE) {
    assert.ok(!pattern.test(code),
      'the ' + name + ' region must stay pure; found "' + label + '" in it');
  }

  return new Function("'use strict';\n" + source + '\nreturn ' + exportName + ';')();
}

// The source of a top-level function in the SPA's inline script, from its
// `function` keyword to its matching close brace. Used for structural checks
// on code that touches the DOM and so cannot be extracted and run.
//
// The brace walk is deliberately naive — it counts braces over comment-stripped
// text with no idea of strings or regex literals, and `stripComments` is itself
// line-oriented and blind to a `//` inside a string. Two earlier versions tried
// to infer the boundary better (a fixed indent, then these braces) and each was
// wrong in a way that turned a caught defect into a green run: the capture ran
// past the function's end, and the call the check demanded was then satisfied
// by the *next* function's declaration line.
//
// So this does not try a third inference. It checks the answer instead: a
// correct capture is exactly one function expression and parses as one, while
// every over-capture drags in a following statement and every under-capture
// leaves a brace or a quote open. Both fail here, whatever went wrong upstream.
// A string- and regex-aware lexer would cost more in test-support code than the
// guard does, and would still need checking.
//
// It is conservative, and that is the trade: a captured function containing a
// `//` inside a string, or an unbalanced brace in a string or regex literal,
// fails here even with nothing wrong in the SPA. That is a loud failure naming
// this helper rather than a silent pass over a real defect, which is the way
// round it should be — but if you hit it while editing one of the captured
// functions, the code is probably fine and this file is what needs teaching.
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
    else if (c === '}' && --depth === 0) return checked(name, htmlWithoutComments.slice(at, i + 1));
  }
  return assert.fail(name + ' has no matching close brace');
}

function checked(name, body) {
  try {
    new Function('return (' + body + ');');
  } catch (err) {
    assert.fail('bodyOf(' + name + ') did not capture exactly one function — ' +
      'the brace walk ran past its end or stopped short: ' + err.message);
  }
  return body;
}

// The SPA's one <style> block, with CSS comments stripped. Shared for the same
// reason the rest of this file is: a test that re-derives the runfiles path and
// re-implements the comment strip drifts from this one silently. Scoped to the
// block rather than the whole document so a `{` in a JS string cannot be walked
// as if it opened a rule.
function stylesheet() {
  const [open, close] = styleBounds(html);
  // CSS has no `//` comment form, so the block form is the whole strip. String
  // literals are blanked as well: a caller walking braces to find rule
  // boundaries would otherwise read a `}` inside `content: "}"` as the end of a
  // rule, which in a stylesheet with an @media block silently promotes a
  // media-nested rule to an unconditional one.
  return html.slice(open + '<style>'.length, close)
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'/g, '""');
}

// Everything outside that <style> block, with JS comments already stripped.
// For the checks that have to look outside any one function — that a named
// handler is registered on an event, say — where `bodyOf` cannot reach and the
// stylesheet would only add false matches.
function scriptSource() {
  const [open, close] = styleBounds(htmlWithoutComments);
  return htmlWithoutComments.slice(0, open) +
    htmlWithoutComments.slice(close + '</style>'.length);
}

// The bounds of the one <style> block, checked the same way for both callers:
// a second block would leave scriptSource() carrying CSS that a regex looking
// for script could match.
function styleBounds(source) {
  const open = source.indexOf('<style>');
  const close = source.indexOf('</style>', open);
  assert.ok(open >= 0 && close > open, 'static/index.html must contain a <style> block');
  assert.strictEqual(source.indexOf('<style>', open + 1), -1,
    'static/index.html must contain exactly one <style> block');
  return [open, close];
}

module.exports = { extract, bodyOf, stylesheet, scriptSource };
