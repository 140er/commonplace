import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
const app = readFileSync('commonplace/web/static/app.js', 'utf8');
const source = app.slice(app.indexOf('// Controllers load'), app.indexOf('import { api,'))
  .replace(/import\("([^"]+)"\)/g, (_, path) => `load(${JSON.stringify(path)})`);
const requested = [];
let failing = false;
const context = {state: {deck: {on: () => true, owns: () => false}}, root: {}, api() {}, refresh() {}, showError() {},
  async loadRouteStyles() {},
  async load(path) {
    requested.push(path);
    if (failing) throw new Error('Module unavailable');
    return {rulesHandlers: () => ({})};
  }, rulesUI: null};
runInNewContext(source + '\nglobalThis.prepare = prepareRoute;', context);
const scenario = process.argv[2];
const paths = () => requested.map(path => path.slice(2, -3));
if (scenario === 'styles') {
  const {loadRouteStyles} = await import('../commonplace/web/static/route-styles.js');
  const links = [], doc = {createElement: () => ({remove() {}}), head: {append(link) {links.push(link);}}};
  let settled = false;
  const first = loadRouteStyles('#/settings', doc).then(() => {settled = true;});
  await Promise.resolve(); assert.equal(settled, false, 'Content must wait for its route CSS');
  assert.equal(links.length, 1); links[0].onload(); await first;
  await loadRouteStyles('#/settings', doc); assert.equal(links.length, 1);
  const failed = loadRouteStyles('#/console', doc); links.at(-1).onerror();
  await assert.rejects(failed, /Could not load screen styles/);
  const retry = loadRouteStyles('#/console', doc); assert.equal(links.length, 3);
  links.at(-1).onload(); await retry;
} else if (scenario === 'retry') {
  failing = true;
  await assert.rejects(context.prepare('#/console'), /Module unavailable/);
  failing = false;
  await context.prepare('#/console');
  assert.deepEqual(paths(), ['console', 'console']);
} else if (scenario === 'single-flight') {
  await Promise.all([context.prepare('#/admin'), context.prepare('#/admin')]);
  assert.deepEqual(paths().sort(), ['admin', 'adminrunners', 'invites']);
} else {
  const pages = {settings: ['projects', 'agent-schedules', 'tool-policy', 'plan', 'agents'], admin: ['admin', 'adminrunners', 'invites'], memory: ['memory', 'board', 'chatdock'], bots: ['bots'], board: ['board', 'children', 'projectpanel', 'chatdock', 'cardmove']};
  await context.prepare(scenario === 'board' ? '#/board/project' : `#/${scenario}`);
  assert.deepEqual(paths().sort(), pages[scenario].sort());
  assert.ok(!paths().includes('chat'), 'A closed chat does not load its transcript controller');
  assert.ok(!paths().includes('details'), 'An unopened drawer does not load its controller');
}
