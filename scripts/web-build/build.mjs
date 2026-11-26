// Deterministic, route-sized ESM delivery. Factories share one module cache across
// bundles, so a later dynamic import never creates another API/session singleton.
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';

// V8's native module parser validates the restricted compiler without loading
// or executing application code. Node 22/24 still gates this API behind a flag.
if (!vm.SourceTextModule) {
  const child = spawnSync(process.execPath, ['--experimental-vm-modules', fileURLToPath(import.meta.url), ...process.argv.slice(2)], { stdio: 'inherit' });
  if (child.error) throw child.error;
  process.exit(child.status ?? 1);
}
const { compile, validate } = await import('./modules.mjs');

const source = resolve(process.argv[2] || fileURLToPath(new URL('../../commonplace/web/static', import.meta.url)));
const output = resolve(process.argv[3] || resolve(source, 'build'));
const hash = bytes => createHash('sha256').update(bytes).digest('hex').slice(0, 16);
const files = (await readdir(source)).filter(name => name.endsWith('.js') && !['boot-guard.js', 'deck-prepaint.js'].includes(name)).sort();
const modules = new Map();
for (const name of files) {
  const input = await readFile(resolve(source, name), 'utf8');
  modules.set(name, compile(name, input));
}
await validate(modules);
function closure(entries, found = new Set()) {
  for (const name of entries) {
    if (found.has(name)) continue;
    if (!modules.has(name)) throw new Error(`Unknown module ${name}`);
    found.add(name); closure(modules.get(name).dependencies, found);
  }
  return [...found].sort();
}
const factories = names => '{' + names.map(name => `${JSON.stringify(name)}:(module,exports,require,__import)=>{${modules.get(name).code}}`).join(',') + '}';
const runtime = `
const factories = new Map(), cache = new Map(), pending = new Map();
const runtime = globalThis.commonplaceModules = {
  add(items) { for (const [name, factory] of Object.entries(items)) if (!factories.has(name)) factories.set(name, factory); },
  require(name) {
    name = name.replace(/^\\.\\//, '');
    if (cache.has(name)) return cache.get(name).exports;
    const factory = factories.get(name);
    if (!factory) throw new Error('Missing screen module: ' + name);
    const module = {exports:{}}; cache.set(name, module);
    try { factory(module, module.exports, runtime.require, runtime.import); }
    catch (error) { cache.delete(name); throw error; }
    return module.exports;
  },
  async import(name) {
    if (factories.has(name)) return runtime.require(name);
    if (!pending.has(name)) pending.set(name, import('/app/build/' + globalThis.commonplaceAssetManifest.modules[name])
      .catch(error => { pending.delete(name); throw error; }));
    await pending.get(name); return runtime.require(name);
  }
};`;
await mkdir(output, { recursive: true });
async function emit(stem, body, suffix = 'js') {
  const name = `${stem}.${hash(body)}.${suffix}`;
  await writeFile(resolve(output, name), body);
  return name;
}
const manifest = { version: 1, routes: {}, modules: {}, styles: [], fonts: [] };
for (const name of files) {
  manifest.modules[name] = await emit(name.slice(0, -3), `globalThis.commonplaceModules.add(${factories(closure([name]))});\n`);
}
const board = ['board.js', 'children.js', 'projectpanel.js', 'chatdock.js', 'cardmove.js'];
const routes = {
  shell: [], bots: ['bots.js'], board,
  card: [...board, 'details.js', 'deck-board.js', 'card-reviewer.js', 'blockers.js', 'lifecycle.js', 'decide.js'],
  memory: ['memory.js', 'board.js', 'chatdock.js'],
  settings: ['projects.js', 'agent-schedules.js', 'tool-policy.js', 'plan.js', 'agents.js'],
  space: [...board, 'decide.js'], chat: [...board, 'chat.js'],
  needs: [...board, 'needs.js', 'details.js'], decide: ['decide.js', 'decide-swipe.js'],
  rules: ['rules.js'], admin: ['admin.js', 'adminrunners.js', 'invites.js'],
  'instance-admin': ['instance-admin.js'], console: ['console.js'],
  today: ['deck-today-capy.js'], compute: ['deck-workspace.js'],
  'compute-map': ['topology-map.js', 'map-discovery.js'], coworkers: ['deck-workspace.js'],
};
for (const [route, entries] of Object.entries(routes)) {
  manifest.routes[route] = await emit(`entry-${route}`, runtime + `\nruntime.add(${factories(closure(['app.js', ...entries]))});\nruntime.require('app.js');\nexport {};\n`);
}
const html = await readFile(resolve(source, 'index.html'), 'utf8');
manifest.styles = [...html.matchAll(/<link rel="stylesheet" href="\/app\/([^"]+)">/g)].map(match => match[1]);
// Route-added sheets follow the original pinned cascade, never @import waterfalls.
const extraStyles = ['deck-home-today.css', 'deck-workspace.css', 'deck-compute-capy.css', 'compute.css', 'coworkers.css', 'deck-memory.css', 'deck-pipeline.css'];
const styles = [...new Set([...manifest.styles, ...extraStyles])];
const css = (await Promise.all(styles.map(async name => `/* ${name} */\n` + await readFile(resolve(source, name), 'utf8')))).join('\n');
manifest.css = await emit('styles', css.replaceAll('url("fonts/', 'url("/app/fonts/'), 'css');
// The two local variable fonts cover the UI; both already declare font-display:swap.
manifest.fonts = ['inter-latin.woff2', 'geist-mono-latin.woff2'];
manifest.styles = styles;
await writeFile(resolve(output, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log(`Built ${Object.keys(manifest.routes).length} route entries and one stylesheet in ${output}`);
