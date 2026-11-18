import assert from 'node:assert/strict';
import { Script } from 'node:vm';
import { compile, validate } from '../scripts/web-build/modules.mjs';

const source = new Map([
  ['api.js', `export const state = { count: 0 }, label = 'exact'; export function increment() { state.count++; }`],
  ['screen.js', `
// import('./comment.js'); export const comment = 'never';
import { state as shared, increment } from './api.js';
export { label as title } from './api.js';
const words = "import('./words.js') and import.meta.url";
const template = \`export const fake = 1; import('./text.js') \${'literal'}\`;
const pattern = /import[(]fixture[)]/;
export const before = shared.count, after = (increment(), shared.count);
export class View { value() { return shared.count; } }
export async function lazy() { return \`value: \${(await import('./lazy.js')).value}\`; }
export const url = import.meta.url;
export function copy() { return [words, template, pattern.source]; }
const keywords = { import: 'Import', export: 'Export' };
`],
  ['lazy.js', `export const value = 7;`],
]);
const modules = new Map([...source].map(([name, text]) => [name, compile(name, text)]));
await validate(modules);
const cache = new Map(), loaded = [];
function require(name) {
  if (cache.has(name)) return cache.get(name);
  loaded.push(name);
  const exports = {}; cache.set(name, exports);
  const factory = new Script(`(function(module,exports,require,__import){"use strict";\n${modules.get(name).code}\n})`).runInThisContext();
  factory({ exports }, exports, require, async name => require(name));
  return exports;
}
globalThis.location = { href: 'https://example.test/app/' };
assert.deepEqual(loaded, []); // Native linking validates without executing code.
const screen = require('screen.js');
assert.equal(screen.before, 0); assert.equal(screen.after, 1);
assert.equal(new screen.View().value(), 1); assert.equal(screen.title, 'exact');
assert.equal(screen.url, 'https://example.test/app/screen.js');
assert.equal(screen.copy()[0], "import('./words.js') and import.meta.url");
assert.equal(screen.copy()[1], "export const fake = 1; import('./text.js') literal");
assert.ok(!loaded.includes('lazy.js'));
assert.equal(await screen.lazy(), 'value: 7');
assert.equal(require('api.js').state.count, 1);
for (const text of ['export let count=0;', 'let count=0; export {count};', 'let {count}={count:0}; export {count};', 'export default 1;', 'import api from "./api.js";']) {
  assert.throws(() => compile('unsupported.js', text), /unsupported module syntax/);
}
const cycle = new Map([
  ['a.js', compile('a.js', `import { b } from './b.js'; export const a = b;`)],
  ['b.js', compile('b.js', `import { a } from './a.js'; export const b = a;`)],
]);
await assert.rejects(validate(cycle), /Static module cycle/);
const missing = new Map([['a.js', compile('a.js', `export async function load() { return import('./missing.js'); }`)]]);
await assert.rejects(validate(missing), /Unknown dynamic module/);
