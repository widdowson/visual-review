// The initials drawn in the signed-in chip, extracted from static/index.html.
//
// Worth its own target because Cloudflare Access gives the SPA an email and
// nothing else — no display name, no avatar — so this function is the entire
// difference between "a person" and "a coloured dot" in the corner of the page.

const assert = require('assert');
const { extract } = require('./spa_source');

const initialsFor = extract('signed-in-chip', 'initialsFor');

// The two addresses this deployment is actually for.
assert.strictEqual(initialsFor('apw@apw.photos'), 'AP');
assert.strictEqual(initialsFor('widdowson@gmail.com'), 'WI');

// A separator in the local part is a name boundary.
assert.strictEqual(initialsFor('andrew.widdowson@apw.photos'), 'AW');
assert.strictEqual(initialsFor('andrew_widdowson@apw.photos'), 'AW');
assert.strictEqual(initialsFor('andrew-widdowson@apw.photos'), 'AW');
assert.strictEqual(initialsFor('andrew+tag@apw.photos'), 'AT');

// Three parts still yields two letters, not three.
assert.strictEqual(initialsFor('a.b.c@apw.photos'), 'AB');

// A single letter would read as a bullet in a 20px circle, so a local part with
// no separator falls back to its first two characters.
assert.strictEqual(initialsFor('a@apw.photos'), 'A');
assert.strictEqual(initialsFor('ab@apw.photos'), 'AB');

// Degenerate input must not throw: the chip is an indicator, and an exception
// here would run inside the SPA's own bootstrap.
assert.strictEqual(initialsFor(''), '?');
assert.strictEqual(initialsFor(null), '?');
assert.strictEqual(initialsFor(undefined), '?');
assert.strictEqual(initialsFor('@apw.photos'), '?');
assert.strictEqual(initialsFor('...@apw.photos'), '?');

console.log('test_initials: all assertions passed');
