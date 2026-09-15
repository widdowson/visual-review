// Shared helper for the tests that exercise logic living inside content.js.
//
// The content script is one IIFE by design, so these tests extract the real
// source from it and run that, rather than keeping a copy — a test holding its
// own copy of the logic keeps passing after the real one changes. This file
// exists because test_content_logic.js held exactly such a copy of
// hasImageExtension, which would have gone on passing after the real one
// started reading a list fetched from the server.
//
// The extracted region is deliberately impure: it reads localStorage, calls
// fetch and reads the clock. So unlike tests/spa_source.js, which asserts its
// region touches none of those, this one names them as parameters of the
// generated function. A parameter shadows the global of the same name inside
// the function body, which is what lets a test drive the real source with a
// stub clock, a stub store and a stub server, and get a fresh copy of the
// region's module state on every call.

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const runfiles = process.env.RUNFILES_DIR || '';
const CONTENT_PATH = runfiles
  ? path.join(runfiles, '_main', 'extension', 'content.js')
  : path.join(__dirname, 'content.js');

const source = fs.readFileSync(CONTENT_PATH, 'utf8');

// The source between `// <name>:begin` and `// <name>:end`. The markers are
// part of the contract: if they go missing this fails rather than silently
// testing nothing. Each must appear exactly once, or the slice below would
// silently take the span between the wrong pair.
function region(name) {
  const beginMarker = name + ':begin';
  const endMarker = name + ':end';

  const begin = source.indexOf(beginMarker);
  const end = source.indexOf(endMarker);
  assert.ok(begin > 0, 'content.js must contain the ' + beginMarker + ' marker');
  assert.ok(end > begin, 'content.js must contain ' + endMarker + ' after ' + beginMarker);
  assert.strictEqual(source.indexOf(beginMarker, begin + 1), -1,
    beginMarker + ' must appear exactly once in content.js');
  assert.strictEqual(source.indexOf(endMarker, end + 1), -1,
    endMarker + ' must appear exactly once in content.js');

  return source.slice(source.indexOf('\n', begin) + 1, source.lastIndexOf('\n', end));
}

// The names the vr:extensions region reads from its surroundings, and the ones
// it defines. Both lists are asserted rather than assumed: a region that stops
// defining one of these fails here, instead of the tests below quietly
// exercising `undefined`.
const INJECTED = ['IMAGE_EXTENSIONS', 'VR_BASE_URL', 'fetch', 'localStorage', 'Date', 'console'];
const EXPORTED = [
  'normalizeExtensions',
  'readCachedExtensions',
  'writeCachedExtensions',
  'ensureExtensions',
  'hasImageExtension',
];

// Evaluate the vr:extensions region against `env`, returning its functions.
// Every call re-evaluates it, so `_extensions` and the memoized promise start
// fresh — two cases in one process cannot leak into each other.
function loadExtensions(env) {
  const src = region('vr:extensions');

  for (const name of EXPORTED) {
    assert.ok(new RegExp('\\bfunction\\s+' + name + '\\s*\\(').test(src),
      'the vr:extensions region must define ' + name);
  }
  for (const name of INJECTED) {
    assert.ok(Object.prototype.hasOwnProperty.call(env, name),
      'loadExtensions needs a stub for ' + name);
  }

  const body = "'use strict';\n" + src +
    '\nreturn {' + EXPORTED.map((n) => n + ': ' + n).join(', ') + '};';
  return new Function(...INJECTED, body)(...INJECTED.map((n) => env[n]));
}

// Comments are stripped before anything is matched below, so a comment saying
// a call is made cannot stand in for the call. Line-oriented and blind to a
// `//` inside a string, same as tests/spa_source.js — content.js has no such
// string today, and a new one shows up as a failure here rather than as a
// silent pass.
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

const sourceWithoutComments = stripComments(source);

// The source of a top-level function in content.js, from its `function`
// keyword (or the `async` before it) to its matching close brace. For the
// functions that touch the DOM and the network and so cannot be extracted and
// run — the only thing available there is a structural check on the text.
//
// The brace walk is naive: it counts braces with no idea of strings or regex
// literals. So rather than trusting it, the capture is checked — a correct one
// is exactly one function expression and parses as one, while an over-capture
// drags in a following statement and an under-capture leaves a brace open.
// This is the same guard, and the same reasoning, as tests/spa_source.js.
function bodyOf(name) {
  const at = sourceWithoutComments.indexOf('function ' + name + '(');
  assert.ok(at >= 0, 'content.js must define ' + name);
  assert.strictEqual(sourceWithoutComments.indexOf('function ' + name + '(', at + 1), -1,
    name + ' must be defined exactly once');

  // Take the `async` with it: the captured text is parsed below, and `await`
  // inside a function that has lost its `async` is a syntax error.
  const asyncAt = sourceWithoutComments.lastIndexOf('async ', at);
  const start = (asyncAt >= 0 && sourceWithoutComments.slice(asyncAt, at).trim() === 'async')
    ? asyncAt
    : at;

  const open = sourceWithoutComments.indexOf('{', at);
  assert.ok(open > at, name + ' must have a body');
  let depth = 0;
  for (let i = open; i < sourceWithoutComments.length; i++) {
    const c = sourceWithoutComments[i];
    if (c === '{') depth++;
    else if (c === '}' && --depth === 0) {
      return checked(name, sourceWithoutComments.slice(start, i + 1));
    }
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

module.exports = {
  region, loadExtensions, bodyOf, source, sourceWithoutComments, INJECTED, EXPORTED,
};
