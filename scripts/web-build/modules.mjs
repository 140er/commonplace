// This is deliberately a compiler for this repository's small module dialect,
// not a general JavaScript transpiler. V8 validates syntax, imports and exports;
// unsupported forms and static cycles fail the build rather than changing meaning.
import { Script, SourceTextModule } from 'node:vm';

// Only module keywords need rewriting. Keep literal/comment bytes untouched,
// including template text, and visit code inside ${...}. Regex literals are
// skipped using the preceding expression token (plus control-parenthesis context).
function tokens(source) {
  const result = [];
  let at = 0, previous = '', expression = true;
  const parentheses = [];
  function code(template = false) {
    let braces = 0;
    while (at < source.length) {
      const start = at, c = source[at], next = source[at + 1];
      if (/\s/.test(c)) { at++; continue; }
      if (c === '/' && next === '/') { at = source.indexOf('\n', at); if (at < 0) at = source.length; continue; }
      if (c === '/' && next === '*') { at = source.indexOf('*/', at + 2) + 2; if (at < 2) throw new Error('Unclosed comment'); continue; }
      if (c === '"' || c === "'") {
        at++;
        while (at < source.length) { const char = source[at++]; if (char === '\\') at++; else if (char === c) break; }
        result.push({ value: source.slice(start, at), start, end: at });
        expression = false; previous = 'literal'; continue;
      }
      if (c === '`') {
        result.push({ value: '(', start, end: start });
        at++;
        while (at < source.length) {
          const char = source[at++];
          if (char === '\\') at++;
          else if (char === '`') break;
          else if (char === '$' && source[at] === '{') { at++; expression = true; code(true); }
        }
        result.push({ value: ')', start: at, end: at });
        expression = false; previous = 'literal'; continue;
      }
      if (c === '/' && expression) {
        at++; let bracket = false;
        while (at < source.length) {
          const char = source[at++];
          if (char === '\\') at++;
          else if (char === '[') bracket = true;
          else if (char === ']') bracket = false;
          else if (char === '/' && !bracket) break;
        }
        while (/[a-z]/i.test(source[at] || '') && at < source.length) at++;
        expression = false; previous = 'literal'; continue;
      }
      if (/[\w$]/.test(c)) {
        at++; while (at < source.length && /[\w$]/.test(source[at])) at++;
      } else {
        at += ['=>', '?.', '++', '--', '&&', '||', '??'].includes(source.slice(at, at + 2)) ? 2 : 1;
      }
      const value = source.slice(start, at);
      if (value === '}' && template && braces === 0) return;
      if (value === '{') braces++;
      if (value === '}') braces--;
      result.push({ value, start, end: at });
      if (value === '(') parentheses.push(['if', 'while', 'for', 'with', 'switch', 'catch'].includes(previous));
      expression = value === ')' ? Boolean(parentheses.pop())
        : /^(?:[({[=,:;!?~+*%&|^<>\/-]|=>|&&|\|\||\?\?|return|throw|case|yield|typeof|void|delete|in|of|else|do)$/.test(value);
      previous = value;
    }
  }
  code();
  return result;
}

export function compile(name, source) {
  const native = new SourceTextModule(source, { identifier: name });
  const list = tokens(source), edits = [], imports = [], exports = new Map();
  const dependencies = [], dynamic = [], mutable = new Set();
  const fail = detail => { throw new Error(`${name}: unsupported module syntax (${detail})`); };
  const path = token => {
    if (!token || !/^(['"])\.\/[a-z\d-]+\.js\1$/i.test(token.value)) fail('relative literal .js path required');
    return token.value.slice(3, -1);
  };
  const bindings = (start, end) => {
    const found = [];
    for (let i = start; i < end;) {
      const original = list[i++]?.value;
      if (!/^[a-z_$][\w$]*$/i.test(original || '')) fail('named bindings required');
      let local = original;
      if (list[i]?.value === 'as') { i++; local = list[i++]?.value; }
      if (!/^[a-z_$][\w$]*$/i.test(local || '')) fail('binding alias');
      found.push([original, local]);
      if (i < end && list[i++]?.value !== ',') fail('binding separator');
    }
    return found;
  };
  for (let i = 0; i < list.length; i++) {
    const token = list[i], value = token.value;
    if (!['import', 'export'].includes(value) || ['.', '?.'].includes(list[i - 1]?.value) || list[i + 1]?.value === ':') continue;
    if (value === 'import' && list[i + 1]?.value === '(') {
      const dependency = path(list[i + 2]);
      if (list[i + 3]?.value !== ')') fail('dynamic import options');
      dynamic.push(dependency);
      edits.push([token.start, list[i + 3].end, `__import(${JSON.stringify(dependency)})`]);
      i += 3; continue;
    }
    if (value === 'import' && list[i + 1]?.value === '.') {
      if (list.slice(i + 2, i + 5).map(t => t.value).join('') !== 'meta.url') fail('import.meta.url required');
      edits.push([token.start, list[i + 4].end, `new URL(${JSON.stringify('/app/' + name)}, location.href).href`]);
      i += 4; continue;
    }
    if (list[i + 1]?.value === '{') {
      let close = i + 2;
      while (close < list.length && list[close].value !== '}') close++;
      const names = bindings(i + 2, close);
      let end = close;
      if (list[close + 1]?.value === 'from') {
        const dependency = path(list[close + 2]); dependencies.push(dependency); end += 2;
        if (value === 'import') imports.push(`const {${names.map(([a, b]) => `${a}:${b}`).join(',')}}=require(${JSON.stringify(dependency)});`);
        else {
          imports.push(`require(${JSON.stringify(dependency)});`);
          for (const [a, b] of names) exports.set(b, `require(${JSON.stringify(dependency)})[${JSON.stringify(a)}]`);
        }
      } else {
        if (value === 'import') fail('import from required');
        for (const [a, b] of names) exports.set(b, a);
      }
      if (list[end + 1]?.value === ';') end++;
      edits.push([token.start, list[end].end, '']); i = end; continue;
    }
    if (value !== 'export') fail('named import required');
    let declaration = i + 1;
    if (list[declaration]?.value === 'async') declaration++;
    if (!['const', 'function', 'class'].includes(list[declaration]?.value)) fail('immutable named export required');
    const exported = list[declaration + 1]?.value;
    if (!/^[a-z_$][\w$]*$/i.test(exported || '')) fail('named declaration required');
    exports.set(exported, exported); edits.push([token.start, token.end, '']);
    if (list[declaration].value === 'const') {
      if (list[declaration + 2]?.value !== '=') fail('simple const binding required');
      let depth = 0;
      for (let j = declaration + 2; j < list.length; j++) {
        const part = list[j].value;
        if (depth === 0 && part === ';') break;
        if (depth === 0 && part === ',') {
          const binding = list[j + 1]?.value;
          if (!/^[a-z_$][\w$]*$/i.test(binding || '') || list[j + 2]?.value !== '=') fail('simple const binding required');
          exports.set(binding, binding);
        }
        if (['(', '[', '{'].includes(part)) depth++;
        if ([')', ']', '}'].includes(part)) depth--;
      }
    }
  }
  let depth = 0;
  for (let i = 0; i < list.length; i++) {
    const value = list[i].value;
    if (depth === 0 && ['let', 'var'].includes(value)) {
      // A mutable local exported through `export {name}` would make a named
      // import snapshot incorrect. Reject it just like `export let` above.
      let nesting = 0;
      for (let j = i; j < list.length; j++) {
        const part = list[j].value;
        if (nesting === 0 && part === ';') break;
        if (nesting === 0 && (j === i || part === ',')) {
          const binding = list[j + 1]?.value;
          if (!/^[a-z_$][\w$]*$/i.test(binding || '')) fail('simple mutable local binding required');
          mutable.add(binding);
        }
        if (['(', '[', '{'].includes(part)) nesting++;
        if ([')', ']', '}'].includes(part)) nesting--;
      }
    }
    if (['(', '[', '{'].includes(value)) depth++;
    if ([')', ']', '}'].includes(value)) depth--;
  }
  for (const local of exports.values()) if (mutable.has(local)) fail('mutable exported binding');
  if (JSON.stringify([...new Set(dependencies)].sort()) !== JSON.stringify([...new Set(native.dependencySpecifiers)].sort().map(p => p.slice(2)))) fail('static dependency mismatch');
  let body = source;
  for (const [start, end, replacement] of edits.sort((a, b) => b[0] - a[0])) body = body.slice(0, start) + replacement + body.slice(end);
  const code = imports.join('\n') + '\n' + body + '\nObject.defineProperties(exports,{' + [...exports].map(([key, value]) => `${JSON.stringify(key)}:{enumerable:true,get:()=>${value}}`).join(',') + '});\n';
  new Script(`(function(module,exports,require,__import){"use strict";\n${code}\n})`, { filename: name });
  return { code, native, exports, dependencies, dynamic };
}

export async function validate(modules) {
  const visited = new Set(), active = new Set();
  function visit(name) {
    if (!modules.has(name)) throw new Error(`Unknown module ${name}`);
    if (active.has(name)) throw new Error(`Static module cycle at ${name}`);
    if (visited.has(name)) return;
    active.add(name);
    for (const dependency of modules.get(name).dependencies) visit(dependency);
    active.delete(name); visited.add(name);
  }
  for (const [name, module] of modules) {
    visit(name);
    for (const dependency of module.dynamic) if (!modules.has(dependency)) throw new Error(`Unknown dynamic module ${dependency}`);
    if (module.native.status === 'unlinked') await module.native.link((path) => modules.get(path.slice(2)).native);
    const actual = Object.getOwnPropertyNames(module.native.namespace).sort();
    if (JSON.stringify(actual) !== JSON.stringify([...module.exports.keys()].sort())) throw new Error(`${name}: unsupported exported declaration`);
  }
}
