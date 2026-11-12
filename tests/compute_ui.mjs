import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const source = name => readFileSync(new URL(`../commonplace/web/static/${name}.js`, import.meta.url), 'utf8');
const moduleURL = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const apiURL = moduleURL(source('api'));
const load = name => import(moduleURL(source(name).replace(/(['"])\.\/api\.js\1/g, JSON.stringify(apiURL))));
const { createCompute, computePayload, computeExecutionHTML } = await load('compute');
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const clock = 1800000000;
// All endpoints and key bytes here are synthetic. No fixture opens a socket.
const pin = { algorithm: 'ssh-ed25519', key: Buffer.alloc(32, 1).toString('base64') };
const gateway = { id: 'g-one', name: 'Fixture gateway', board: 'demo', address: '192.0.2.10', port: 22, account: 'fixture', host_key: pin, credential_id: 'fixture-gateway-key', credential_version: 1, epoch: 1, state: 'pending_verification', created_at: clock };
const node = { ...gateway, id: 'n-one', name: 'Fixture target', gateway_id: gateway.id, interface: 'fixture0', address: '10.255.255.10', credential_id: 'fixture-target-key' };
const scope = { id: 's-one', context: { workspace: 'w-one', project: 'demo', gateway: gateway.id, user: 'u-one', agent: 'user:u-one', task: '42' }, cidrs: ['10.255.255.0/28'], interfaces: ['fixture0'], methods: ['passive'], expires_at: clock + 900, limits: { hosts: 16, concurrency: 1, rate_per_second: 1, duration_seconds: 30, results: 16 }, revoked: false };
const execution = (state = 'awaiting_approval', extra = {}) => ({ id: 'e-one', node_id: node.id, gateway_id: gateway.id, board: 'demo', task_number: 42, operation: 'system.info.v1', arguments: {}, digest: 'a'.repeat(64), state, limits: { runtime_seconds: 30, output_bytes: 65536 }, reason: '', created_at: clock, updated_at: clock, ...extra });
const inventory = (extra = {}) => ({ gateways: [gateway], nodes: [node], scopes: [scope], executions: [execution()], can_manage: true, capabilities: { transport: false, passive_discovery: false, scripts: false }, ...extra });
const gatewayValues = () => ({ board: 'demo', name: 'Fixture gateway', address: '192.0.2.10', port: '22', account: 'fixture', host_key_algorithm: pin.algorithm, host_key_key: pin.key, credential_id: 'fixture-gateway-key', credential_version: '1', pin_confirmed: 'on' });
const scopeValues = () => ({ gateway_id: gateway.id, task_number: '42', cidrs: '10.255.255.0/28,\nfc00::/120', interfaces: 'fixture0\nfixture1', hosts: '16', concurrency: '1', rate_per_second: '1', duration_seconds: '30', results: '16', expires_minutes: '15', scope_confirmed: 'on' });
const proposalValues = () => ({ node_id: node.id, task_number: '42' });
const failure = (status, code) => Object.assign(new Error(code), { status, payload: { error: code } });

// This intentionally narrow DOM fake runs the production controller. Requests
// ignore AbortSignal to prove the response fences independently of cancellation.
function fixture({ passive = { discoveries: [], capabilities: { passive_discovery: false } }, scripts = [], currentTime = clock } = {}) {
  let html = '', user = { workspace: 'w-one', actor: 'user:u-one', can_admin: true }, hash = '#/compute', sequence = 0;
  const listeners = new Map(), requests = [], boardsSeen = [];
  const doc = new EventTarget(), events = new EventTarget(), confirmation = { checked: true };
  const host = {
    get innerHTML() { return html; }, set innerHTML(value) { html = value; },
    addEventListener(kind, fn) { listeners.set(kind, fn); }, removeEventListener(kind) { listeners.delete(kind); },
    querySelector(selector) { return selector === '[name="pin_confirmed"]' ? confirmation : selector === '#compute-editor-heading' ? { focus() {} } : null; },
  };
  const api = (path, options = {}) => {
    if (path === "/api/compute/scripts" && !options.method) return Promise.resolve({ scripts: structuredClone(scripts) });
    if (path === "/api/compute/discoveries" && !options.method) return Promise.resolve(structuredClone(passive));
    let yes, no;
    const promise = new Promise((resolve, reject) => { yes = resolve; no = reject; });
    const request = { path, options, settled: false, resolve(value) { request.settled = true; yes(structuredClone(value)); }, reject(error) { request.settled = true; no(error); } };
    requests.push(request); return promise;
  };
  const ui = createCompute({ mount: () => host, api, current: () => user, route: () => hash, onBoards: boards => boardsSeen.push(boards), doc, events, now: () => currentTime, requestId: () => `request-${++sequence}` });
  const take = (path, method = 'GET') => {
    const request = requests.find(item => !item.taken && !item.settled && item.path === path && (item.options.method || 'GET') === method);
    assert.ok(request, `${method} ${path} was requested`); request.taken = true; return request;
  };
  const click = (action, id) => listeners.get('click')?.({ target: { closest: () => ({ dataset: { computeAction: action, id } }) } });
  const submit = (kind, values) => {
    const form = { dataset: { computeForm: kind }, entries: Object.entries(values) };
    listeners.get('submit')?.({ target: { closest: () => form }, preventDefault() {}, stopPropagation() {} });
  };
  const input = (name, value, type = 'text') => listeners.get('input')?.({ target: { name, type, value, checked: value } });
  const respond = async (data = inventory(), boards = [{ slug: 'demo', name: 'Fixture project' }]) => {
    take('/api/compute').resolve(data); take('/api/boards').resolve({ boards }); await flush();
  };
  const open = async (data, boards) => { ui.show(); await respond(data, boards); };
  return { ui, host, doc, events, requests, listeners, boardsSeen, confirmation, take, click, submit, input, open, respond, setUser(value) { user = value; }, setRoute(value) { hash = value; } };
}

const cases = {
  async escaping() {
    const f = fixture(), attack = '<img src=x onerror="alert(1)"> & <script>bad()</script>';
    await f.open(inventory({ gateways: [{ ...gateway, name: attack, board: attack, address: attack, account: attack, credential_id: attack, host_key: { algorithm: attack, key: attack } }], nodes: [{ ...node, name: attack, interface: attack }], scopes: [{ ...scope, cidrs: [attack], interfaces: [attack], methods: [attack], context: { ...scope.context, agent: attack, project: attack } }], executions: [execution('awaiting_approval', { operation: attack, arguments: { value: attack }, reason: attack })] }), [{ slug: attack, name: attack }]);
    assert.doesNotMatch(f.host.innerHTML, /<img|<script>/);
    assert.match(f.host.innerHTML, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt; &amp;/);
    assert.match(f.host.innerHTML, /href="#\/card\/42"/);
    assert.doesNotMatch(computeExecutionHTML(execution('failed', { task_number: 'javascript:alert(1)', digest: attack })), /href="javascript:/);
    const running = execution('approved', { run: { phase: 'running', reason: 'active', certainty: 'unknown', capacity_reserved: true, cancel_requested_at: null, result: { output: attack } } });
    const runningHTML = computeExecutionHTML(running);
    assert.match(runningHTML, /Execution running/);
    assert.match(runningHTML, /Request cancellation/);
    assert.doesNotMatch(runningHTML, /Approved · automatic execution authorized|<img|<script>/);
    const cancelled = computeExecutionHTML({ ...running, run: { ...running.run, cancel_requested_at: 1000 } });
    assert.match(cancelled, /termination unconfirmed/);
    assert.doesNotMatch(cancelled, /data-compute-action="cancel"/);
    const completed = computeExecutionHTML({ ...running, run: { ...running.run, phase: 'terminal', exit_code: 0, capacity_reserved: false } });
    assert.match(completed, /Exit observed · code 0/);
    assert.doesNotMatch(completed, /data-compute-action="cancel"|capacity remains reserved/i);
    f.click('gateway'); assert.doesNotMatch(f.host.innerHTML, /<img|<script>/);
    f.click('approve', 'e-one'); assert.doesNotMatch(f.host.innerHTML, /<img|<script>/);
    f.ui.stop();
  },

  async payloads() {
    const env = { now: clock, requestId: 'request-fixed' };
    assert.deepEqual(computePayload('gateway', { ...gatewayValues(), workspace: 'foreign', verified: true }, env), { board: 'demo', name: gateway.name, address: gateway.address, port: 22, account: 'fixture', host_key: pin, credential_id: gateway.credential_id, credential_version: 1, pin_confirmed: true });
    const target = { ...gatewayValues(), gateway_id: gateway.id, interface: 'fixture0', address: node.address };
    const targetPayload = computePayload('node', target, env);
    assert.equal(targetPayload.gateway_id, gateway.id); assert.equal(targetPayload.interface, 'fixture0'); assert.ok(!('board' in targetPayload));
    for (const field of ['port', 'credential_version']) for (const invalid of ['', '0', '-1', 'NaN', 'Infinity', '1e3', '1.2', '9007199254740992']) assert.throws(() => computePayload('gateway', { ...gatewayValues(), [field]: invalid }, env), Error);
    for (const change of [{ pin_confirmed: false }, { host_key_key: 'SHA256:unchecked' }, { host_key_key: '-----BEGIN PRIVATE KEY-----' }, { host_key_algorithm: 'unsupported' }, { address: 'fixture.example' }, { port: '65536' }]) assert.throws(() => computePayload('gateway', { ...gatewayValues(), ...change }, env), Error);
    assert.throws(() => computePayload('scope', { ...scopeValues(), concurrency: '17' }, env), /Concurrency/);
    assert.throws(() => computePayload('scope', { ...scopeValues(), methods: 'active' }, env), /passive/);
    assert.throws(() => computePayload('scope', { ...scopeValues(), hosts: '4097' }, env), Error);
    assert.throws(() => computePayload('scope', { ...scopeValues(), expires_minutes: 'Infinity' }, env), Error);
    assert.throws(() => computePayload('scope', { ...scopeValues(), interfaces: 'fixture0,fixture0' }, env), Error);
    assert.throws(() => computePayload('scope', { ...scopeValues(), scope_confirmed: false }, env), Error);
    assert.throws(() => computePayload('scope', scopeValues(), { now: NaN }), Error);
    const grant = computePayload('scope', { ...scopeValues(), agent_id: 'conn:existing', user: 'foreign' }, env);
    assert.deepEqual(grant.methods, ['passive']); assert.equal(grant.agent_id, 'conn:existing'); assert.equal(grant.expires_at, clock + 900); assert.ok(!('user' in grant));
    assert.deepEqual(grant.cidrs, ['10.255.255.0/28', 'fc00::/120']); assert.deepEqual(grant.interfaces, ['fixture0', 'fixture1']);
    assert.deepEqual(computePayload('proposal', { ...proposalValues(), arguments: { shell: 'ignored' }, verified: true }, env), { node_id: node.id, task_number: 42, operation: 'system.info.v1', arguments: {}, request_id: 'request-fixed' });
    assert.throws(() => computePayload('proposal', { ...proposalValues(), operation: 'shell' }, env), /system.info/);
    assert.throws(() => computePayload('approve', { expected_digest: 'a'.repeat(63), expires_minutes: '1' }, env), /digest/);
    assert.throws(() => computePayload('approve', { expected_digest: 'a'.repeat(64), expires_minutes: '6' }, env), Error);
    assert.deepEqual(computePayload('approve', { expected_digest: 'a'.repeat(64), expires_minutes: '2', approved: true }, env), { expected_digest: 'a'.repeat(64), expires_at: clock + 120 });
  },

  async enrollment() {
    const f = fixture(); await f.open(inventory({ gateways: [], nodes: [], scopes: [], executions: [] }));
    assert.match(f.host.innerHTML, /No gateways/); assert.match(f.host.innerHTML, /Add a gateway before/);
    f.click('node'); assert.doesNotMatch(f.host.innerHTML, /data-compute-form="node"/);
    f.click('gateway'); f.submit('gateway', { ...gatewayValues(), pin_confirmed: '' });
    assert.equal(f.requests.filter(row => row.options.method === 'POST').length, 0);
    f.submit('gateway', gatewayValues());
    const create = f.take('/api/compute/gateways', 'POST');
    assert.deepEqual(create.options.body, computePayload('gateway', gatewayValues(), { now: clock }));
    assert.ok(create.options.signal); assert.equal(create.options.timeoutMs, 10000);
    assert.match(f.host.innerHTML, /Saving…/); assert.doesNotMatch(f.host.innerHTML, /data-state="pending_verification"/);
    f.submit('gateway', gatewayValues()); assert.equal(f.requests.filter(row => row.options.method === 'POST').length, 1);
    create.resolve({ gateway }); await flush(); assert.match(f.host.innerHTML, /Pending verification/);
    f.click('node'); f.input('pin_confirmed', true, 'checkbox'); f.input('address', '10.255.255.11');
    assert.equal(f.confirmation.checked, false, 'Editing endpoint/key metadata invalidates the public pin acknowledgement');
    const values = { ...gatewayValues(), gateway_id: gateway.id, address: node.address, name: node.name, interface: node.interface, credential_id: node.credential_id };
    f.submit('node', values); const enroll = f.take('/api/compute/nodes', 'POST');
    assert.deepEqual(enroll.options.body, computePayload('node', values, { now: clock }));
    enroll.resolve({ node }); await flush(); assert.match(f.host.innerHTML, /Fixture target/); assert.match(f.host.innerHTML, /No connection verified/);
    assert.doesNotMatch(f.host.innerHTML, /type="password"|name="private_key"|name="shell"/);
    f.ui.stop();
  },

  async scopes() {
    const f = fixture(); await f.open(inventory({ scopes: [] })); f.click('scope');
    f.submit('scope', scopeValues()); const grant = f.take('/api/compute/scopes', 'POST');
    assert.deepEqual(grant.options.body, computePayload('scope', scopeValues(), { now: clock }));
    assert.ok(!('agent_id' in grant.options.body), 'Blank actor is the current person, not an invented bot');
    grant.resolve({ scope }); await flush(); assert.match(f.host.innerHTML, /Passive grant recorded/);
    assert.match(f.host.innerHTML, /No observations collected/);
    f.click('revoke', scope.id); const revoke = f.take(`/api/compute/scopes/${scope.id}/revoke`, 'POST'); assert.deepEqual(revoke.options.body, {});
    f.click('revoke', scope.id); assert.equal(f.requests.filter(row => row.path.endsWith('/revoke')).length, 1);
    revoke.resolve({ scope: { ...scope, revoked: true } }); await flush(); assert.match(f.host.innerHTML, />Revoked</);
    f.click('revoke', scope.id); assert.equal(f.requests.filter(row => row.path.endsWith('/revoke')).length, 1);
    assert.ok(f.requests.every(row => !/discover|collect|scan/.test(row.path)));
    f.ui.stop();
  },

  async proposals() {
    const f = fixture(); await f.open(inventory({ executions: [] })); f.click('proposal'); f.submit('proposal', proposalValues());
    const first = f.take('/api/compute/executions', 'POST'); assert.deepEqual(first.options.body, computePayload('proposal', proposalValues(), { now: clock, requestId: 'request-1' }));
    assert.doesNotMatch(f.host.innerHTML, /data-state="awaiting_approval"/);
    first.reject(new Error('lost receipt')); await flush(); assert.match(f.host.innerHTML, /reuse its original request ID/);
    f.submit('proposal', proposalValues()); const retry = f.take('/api/compute/executions', 'POST'); assert.deepEqual(retry.options.body, first.options.body);
    retry.resolve({ execution: execution('approved') }); await flush();
    assert.match(f.host.innerHTML, /Approved · automatic execution authorized/); assert.doesNotMatch(f.host.innerHTML, /Server state: Awaiting approval/);
    f.click('proposal'); f.submit('proposal', { node_id: node.id, task_number: '43' });
    const next = f.take('/api/compute/executions', 'POST'); assert.notEqual(next.options.body.request_id, first.options.body.request_id);
    next.resolve({ execution: execution('awaiting_approval', { id: 'e-two', task_number: 43 }) }); await flush();
    f.click('cancel', 'e-two'); const cancel = f.take('/api/compute/executions/e-two/cancel', 'POST'); assert.deepEqual(cancel.options.body, {});
    assert.match(f.host.innerHTML, /Awaiting approval/, 'No optimistic cancellation');
    cancel.resolve({ execution: execution('cancelled', { id: 'e-two', task_number: 43 }) }); await flush(); assert.match(f.host.innerHTML, /Cancelled before dispatch/);
    assert.ok(f.requests.every(row => !/dispatch|run|verify/.test(row.path)));
    f.ui.stop();
  },

  async approval() {
    const f = fixture(); await f.open();
    assert.match(f.host.innerHTML, /automatic execution worker is currently unavailable/i);
    assert.match(f.host.innerHTML, /if the worker becomes available before approval expires/);
    f.click('approve', 'e-one');
    assert.match(f.host.innerHTML, /data-compute-form="approve"/); assert.match(f.host.innerHTML, /fixture-gateway-key/); assert.match(f.host.innerHTML, /fixture-target-key/); assert.match(f.host.innerHTML, new RegExp(pin.key));
    f.submit('approve', { expected_digest: 'b'.repeat(64), expires_minutes: '2' }); assert.equal(f.requests.filter(row => row.options.method === 'POST').length, 0);
    assert.match(f.host.innerHTML, /does not match/);
    f.submit('approve', { expected_digest: execution().digest, expires_minutes: '2' });
    const approve = f.take('/api/compute/executions/e-one/approve', 'POST'); assert.deepEqual(approve.options.body, { expected_digest: execution().digest, expires_at: clock + 120 });
    f.click('cancel', 'e-one'); assert.equal(f.requests.filter(row => row.options.method === 'POST').length, 1);
    approve.resolve({ execution: execution('approved') }); await flush(); assert.match(f.host.innerHTML, /Approved · automatic execution authorized/); assert.doesNotMatch(f.host.innerHTML, /data-compute-action="approve"/);
    f.click('cancel', 'e-one'); f.take('/api/compute/executions/e-one/cancel', 'POST').resolve({ execution: execution('cancelled') }); await flush();
    assert.match(f.host.innerHTML, /Cancelled before dispatch/); assert.doesNotMatch(f.host.innerHTML, /data-compute-action="cancel"/);
    f.ui.stop();

    const stale = fixture(); await stale.open(); stale.click('approve', 'e-one'); const refresh = stale.ui.refresh();
    await stale.respond(inventory({ executions: [execution('awaiting_approval', { digest: 'b'.repeat(64) })] })); await refresh;
    assert.doesNotMatch(stale.host.innerHTML, /data-compute-form="approve"/); assert.match(stale.host.innerHTML, /proposal changed/);
    stale.submit('approve', { expected_digest: execution().digest, expires_minutes: '1' }); assert.equal(stale.requests.filter(row => row.options.method === 'POST').length, 0);
    stale.ui.stop();
  },

  async isolation() {
    for (const change of ['route', 'workspace', 'identity', 'session']) {
      const f = fixture(); await f.open(); f.click('gateway'); f.submit('gateway', gatewayValues()); const old = f.take('/api/compute/gateways', 'POST');
      if (change === 'route') { f.setRoute('#/coworkers'); f.events.dispatchEvent(new Event('hashchange')); }
      if (change === 'workspace') f.setUser({ workspace: 'w-two', actor: 'user:u-one' });
      if (change === 'identity') f.setUser({ workspace: 'w-one', actor: 'user:u-two' });
      if (change === 'session') f.setUser({ workspace: 'w-one', actor: 'user:u-one', can_admin: true });
      old.resolve({ gateway: { ...gateway, name: 'PRIVATE_LATE_GATEWAY' } }); await flush();
      assert.equal(f.host.innerHTML, '', `${change} clears private DOM`); assert.equal(f.listeners.size, 0); assert.ok(old.options.signal.aborted || old.settled);
    }
    const f = fixture(); f.ui.show(); const old = f.take('/api/compute'), oldBoards = f.take('/api/boards');
    f.setUser({ workspace: 'w-two', actor: 'user:u-two' }); f.ui.show();
    old.resolve(inventory({ gateways: [{ ...gateway, name: 'PRIVATE_OLD_WORKSPACE' }] })); oldBoards.resolve({ boards: [{ slug: 'PRIVATE_OLD_BOARD' }] }); await flush();
    assert.ok(old.options.signal.aborted); assert.equal(f.boardsSeen.length, 0);
    await f.respond(inventory({ gateways: [], nodes: [], scopes: [], executions: [] }), []);
    assert.doesNotMatch(f.host.innerHTML, /PRIVATE_OLD_WORKSPACE|PRIVATE_OLD_BOARD/); assert.deepEqual(f.boardsSeen, [[]]);
    f.ui.stop();

    const late = fixture(); await late.open(); late.click('proposal'); late.submit('proposal', proposalValues());
    const oldPost = late.take('/api/compute/executions', 'POST');
    late.setUser({ workspace: 'w-two', actor: 'user:u-two' }); late.ui.show(); await late.respond();
    late.click('gateway'); oldPost.reject(failure(403, 'owner_required')); await flush();
    assert.match(late.host.innerHTML, /data-compute-form="gateway"/, 'An old action refusal cannot clear a newer session');
    assert.doesNotMatch(late.host.innerHTML, /requires the current workspace owner/); late.ui.stop();
  },

  async lifecycle() {
    for (const kind of ['expiry', 'pagehide', 'stop']) {
      const f = fixture(); f.ui.show(); const read = f.take('/api/compute'), projects = f.take('/api/boards');
      if (kind === 'expiry') f.doc.dispatchEvent(new Event('session-expired'));
      if (kind === 'pagehide') f.events.dispatchEvent(new Event('pagehide'));
      if (kind === 'stop') f.ui.stop();
      assert.ok(read.options.signal.aborted); assert.ok(projects.options.signal.aborted); assert.equal(f.listeners.size, 0); assert.equal(f.host.innerHTML, '');
      read.resolve(inventory()); projects.resolve({ boards: [{ slug: 'PRIVATE_LATE_BOARD' }] }); await flush(); assert.equal(f.host.innerHTML, ''); assert.equal(f.boardsSeen.length, 0);
    }
    const f = fixture(); f.ui.show(); const first = f.take('/api/compute'), projects = f.take('/api/boards');
    first.resolve(inventory()); await flush(); assert.doesNotMatch(f.host.innerHTML, /data-compute-action="gateway"/, 'Wait for scoped initial read fan-in');
    projects.resolve({ boards: [{ slug: 'demo' }] }); await flush();
    f.click('approve', 'e-one'); const refresh = f.ui.refresh(); f.click('cancel', 'e-one');
    assert.equal(f.requests.filter(row => row.options.method === 'POST').length, 0, 'Reading cannot race a mutation');
    await f.respond(); await refresh; f.ui.stop();
  },

  async failures() {
    const member = fixture(); await member.open(inventory({ can_manage: false }));
    assert.match(member.host.innerHTML, /requires the current workspace owner/); member.click('gateway'); member.submit('gateway', gatewayValues());
    assert.equal(member.requests.filter(row => row.options.method === 'POST').length, 0); member.ui.stop();
    for (const status of [401, 403, 404]) {
      const f = fixture(); await f.open(); f.click('approve', 'e-one'); f.submit('approve', { expected_digest: execution().digest, expires_minutes: '1' });
      f.take('/api/compute/executions/e-one/approve', 'POST').reject(failure(status, status === 401 ? 'sign_in' : status === 403 ? 'owner_required' : 'not_found')); await flush();
      assert.doesNotMatch(f.host.innerHTML, /Fixture target|fixture-target-key|data-compute-form=/); f.ui.stop();
    }
    const f = fixture(); await f.open(); f.click('gateway'); f.submit('gateway', gatewayValues()); f.take('/api/compute/gateways', 'POST').reject(new Error('unknown')); await flush();
    const posts = () => f.requests.filter(row => row.options.method === 'POST').length;
    f.submit('gateway', gatewayValues()); assert.equal(posts(), 1, 'An ambiguous non-idempotent create is not replayed');
    f.click('close'); f.click('gateway'); f.click('cancel', 'e-one'); assert.equal(posts(), 1);
    assert.match(f.host.innerHTML, /Reload inventory/); f.click('refresh'); await f.respond(); f.click('gateway'); assert.match(f.host.innerHTML, /data-compute-form="gateway"/); f.ui.stop();

    const partial = fixture(); partial.ui.show(); partial.take('/api/compute').resolve(inventory()); partial.take('/api/boards').reject(failure(503, 'unavailable')); await flush();
    assert.match(partial.host.innerHTML, /Fixture target/); assert.match(partial.host.innerHTML, /Projects could not be loaded/);
    assert.equal(partial.boardsSeen.length, 0, 'A failed project read must not wipe the shell’s existing project navigation');
    partial.click('gateway'); assert.doesNotMatch(partial.host.innerHTML, /data-compute-form="gateway"/); partial.ui.stop();
  },

  async scripts() {
    const source = '  print("<script>inert</script>")\n';
    const bundle = { id: 'bundle-one', node_id: node.id, task_number: 42, digest: 'b'.repeat(64), manifest: { runtime: 'python3.v1', privilege: 'unprivileged', arguments: ['; literal $(id)'], output_paths: ['result.txt'] } };
    const payload = computePayload('script', { node_id: node.id, task_number: '42', source, arguments: '["; literal $(id)"]', output_paths: 'result.txt' }, { requestId: 'fixed' });
    assert.equal(payload.source, source); assert.deepEqual(payload.arguments, ['; literal $(id)']); assert.deepEqual(payload.output_paths, ['result.txt']);
    for (const bad of [{ source: 'é'.repeat(16385) }, { source: '\ud800' }, { source: 'x\0y' }, { arguments: '[1]' }, { arguments: '["' + 'é'.repeat(129) + '"]' }, { output_paths: '../outside' }, { output_paths: 'a,a/b' }]) assert.throws(() => computePayload('script', { node_id: node.id, task_number: '42', source, arguments: '[]', ...bad }, { requestId: 'fixed' }));
    assert.throws(() => computePayload('script', { node_id: node.id, task_number: '42', source: '\\'.repeat(32768), arguments: '[]' }, { requestId: 'fixed' }), /64 KiB/);
    const f = fixture(); await f.open(); f.click('script');
    f.submit('script', { node_id: node.id, task_number: '42', source, arguments: '["; literal $(id)"]', output_paths: 'result.txt' });
    const first = f.take('/api/compute/scripts', 'POST'); first.reject(new Error('lost response')); await flush();
    f.submit('script', { node_id: node.id, task_number: '42', source, arguments: '["; literal $(id)"]', output_paths: 'result.txt' });
    const retry = f.take('/api/compute/scripts', 'POST'); assert.deepEqual(retry.options.body, first.options.body);
    retry.resolve({ script: bundle }); await flush();
    f.click('inspect-script', bundle.id); f.take('/api/compute/scripts/bundle-one').resolve({ script: { ...bundle, source } }); await flush();
    assert.match(f.host.innerHTML, /Exact script source/); assert.doesNotMatch(f.host.innerHTML, /<script>inert/);
    f.click('script-proposal', bundle.id); f.submit('script-proposal', {});
    const proposal = f.take('/api/compute/executions', 'POST');
    assert.deepEqual(proposal.options.body, { node_id: node.id, task_number: 42, operation: 'workspace.script.v1', arguments: { bundle_id: bundle.id }, request_id: 'request-2' });
    proposal.resolve({ execution: execution('awaiting_approval', { operation: 'workspace.script.v1', arguments: { bundle_id: bundle.id } }) }); await flush(); f.ui.stop();
    const late = fixture({ scripts: [bundle] }); await late.open(); late.click('inspect-script', bundle.id);
    const pending = late.take('/api/compute/scripts/bundle-one'); late.setRoute('#/coworkers'); late.events.dispatchEvent(new Event('hashchange'));
    pending.resolve({ script: { ...bundle, source: 'PRIVATE_LATE_SOURCE' } }); await flush(); assert.equal(late.host.innerHTML, '');
  },

  async 'enabled-executor'() {
    const payload = JSON.parse(readFileSync(0, 'utf8'));
    assert.equal(payload.inventory.capabilities.transport, true);
    const f = fixture({ currentTime: payload.now });
    await f.open(payload.inventory);
    assert.match(f.host.innerHTML, /automatic execution worker is available/i);
    assert.doesNotMatch(f.host.innerHTML, /Remote dispatch is unavailable|Record approval without dispatch/);
    const proposal = payload.inventory.executions[0];
    f.click('approve', proposal.id);
    assert.match(f.host.innerHTML, />Approve automatic execution<\/button>/);
    assert.match(f.host.innerHTML, /run this exact operation automatically in the background over SSH/);
    assert.match(f.host.innerHTML, /No additional dispatch confirmation is required/);
    f.submit('approve', { expected_digest: proposal.digest, expires_minutes: '2' });
    const approval = f.take(`/api/compute/executions/${proposal.id}/approve`, 'POST');
    const body = approval.options.body;
    approval.resolve({ execution: { ...proposal, state: 'approved' } });
    await flush();
    assert.match(f.host.innerHTML, /Approved · automatic execution authorized/);
    assert.equal(f.requests.filter(row => row.options.method === 'POST').length, 1);
    f.ui.stop();
    console.log(JSON.stringify(body));
  },

  async 'discovery-cancellation'() {
    for (const state of ['queued', 'claimed', 'dispatching', 'uncertain']) {
      const row = { id: 'd-one', scope_id: scope.id, board: 'demo', task_number: 42, state, observations: [], usage: {} };
      const f = fixture({ passive: { discoveries: [row], capabilities: { passive_discovery: true } } });
      await f.open();
      assert.match(f.host.innerHTML, /data-compute-action="discovery-cancel"/, state);
      assert.match(f.host.innerHTML, /A cancellation request does not confirm remote termination/, state);
      f.click('discovery-cancel', row.id);
      const write = f.take('/api/compute/discoveries/d-one/cancel', 'POST');
      assert.deepEqual(write.options.body, {});
      write.resolve({ discovery: { ...row, state: state === 'queued' ? 'cancelled' : state, reason: state === 'queued' ? 'cancelled_before_dispatch' : 'cancellation_requested' } });
      await flush();
      assert.doesNotMatch(f.host.innerHTML, /data-compute-action="discovery-cancel"/, state);
      if (state !== 'queued') assert.match(f.host.innerHTML, /Cancellation requested; remote termination is unconfirmed/);
      f.ui.stop();
    }
    for (const state of ['completed', 'cancelled', 'denied', 'running']) {
      const f = fixture({ passive: { discoveries: [{ id: 'd-one', state }], capabilities: { passive_discovery: true } } });
      await f.open(); f.click('discovery-cancel', 'd-one');
      assert.doesNotMatch(f.host.innerHTML, /data-compute-action="discovery-cancel"/, state);
      assert.equal(f.requests.filter(row => row.options.method === 'POST').length, 0);
      f.ui.stop();
    }
  },

  async discovery() {
    const f = fixture({ passive: { discoveries: [], capabilities: { passive_discovery: true } } });
    await f.open(); f.click('discovery');
    assert.match(f.host.innerHTML, /permanently charges/);
    f.submit('discovery', { scope_id: scope.id, sources: 'linux_arp' });
    const write = f.take('/api/compute/discoveries', 'POST');
    assert.deepEqual(write.options.body, { scope_id: scope.id, sources: ['linux_arp'], request_id: 'request-1' });
    write.reject(new Error('lost response')); await flush();
    f.submit('discovery', { scope_id: scope.id, sources: 'linux_arp' });
    const retry = f.take('/api/compute/discoveries', 'POST');
    assert.deepEqual(retry.options.body, write.options.body);
    const row = { id: 'd-one', scope_id: scope.id, board: 'demo', task_number: 42, state: 'queued', observations: [], usage: {} };
    retry.resolve({ discovery: row }); await flush();
    assert.match(f.host.innerHTML, /Passive collection: queued/i);
    f.click('discovery-cancel', row.id);
    f.take('/api/compute/discoveries/d-one/cancel', 'POST').resolve({ discovery: { ...row, state: 'uncertain', observations: [{ metadata: '<img src=x onerror=bad()>' }] } });
    await flush(); assert.match(f.host.innerHTML, /Outcome unknown/); assert.doesNotMatch(f.host.innerHTML, /<img/);
    f.ui.stop();
    const off = fixture(); await off.open(); off.click('discovery');
    assert.doesNotMatch(off.host.innerHTML, /data-compute-form="discovery"/); off.ui.stop();
  },

  async routing() {
    const { renderRail, renderPhoneTabs } = await load('rail');
    assert.match(renderRail({ can_admin: true, view: 'compute' }), /href="#\/compute"[^>]*aria-current="page"/);
    assert.doesNotMatch(renderRail({ can_admin: false }), /href="#\/compute"/);
    assert.match(renderRail(), /href="#\/coworkers"/); assert.match(renderPhoneTabs({ board: { slug: 'demo' } }), /href="#\/board\/demo\/chat"/);
    const palette = source('palette').replace(/import \{ cardId \} from '.\/board.js';/, 'const cardId = () => "";');
    const { paletteItems } = await import(moduleURL(palette.replace(/(['"])\.\/api\.js\1/g, JSON.stringify(apiURL))));
    assert.ok(paletteItems({ query: 'compute', can_admin: true }).some(item => item.href === '#/compute'));
    assert.ok(!paletteItems({ query: 'compute', can_admin: false }).some(item => item.href === '#/compute'));
    const app = source('app'), slice = (start, end) => app.slice(app.indexOf(start), app.indexOf(end, app.indexOf(start)));
    let shared = 0, shows = 0, stops = 0;
    const state = { me: { workspace: 'w-one' }, screen: 0, route: '#/', base: '#/', boards: [], chatVisible: new Set() };
    const nodes = { '#content': { children: [], innerHTML: '' }, '#card-panel': { hidden: true }, '#shell-dialog': { close() {} } };
    const context = { state, root: { querySelector: selector => nodes[selector] || null }, location: { hash: '#/compute' }, history: { replaceState() {} }, localStorage: { getItem: () => null }, remember() {}, resolveRoute: hash => hash, boardPreference: () => '', decideStart: () => undefined, clearAgentSecret() {}, voice: { stop() {} }, speech: { stop() {} }, markOpenCard() {}, showError() {}, message: value => value,
      computeUI: { show() { shows++; }, stop() { stops++; } }, coworkersUI: null, adminUI: null, consoleUI: null, detailsUI: null, boardDispose: null, title() {}, navigation() {}, async refresh() { shared++; }, api() { throw new Error('Compute must bypass unrelated shell prerequisites'); } };
    runInNewContext(slice('function computeScreen()', 'async function refresh(') + slice('async function route()', '// The current view'), context);
    await context.route(); assert.equal(state.base, '#/compute'); assert.equal(shows, 1); assert.equal(shared, 0);
    context.location.hash = '#/board/demo'; await context.route(); assert.equal(stops, 1); assert.equal(shared, 1);
    state.base = '#/compute'; state.route = '#/compute'; context.location.hash = '#/card/42'; await context.route(); assert.notEqual(state.base, '#/compute'); assert.equal(shared, 2);
    state.route = '#/compute'; runInNewContext(slice('async function refresh(', 'function decideStart('), context); await context.refresh({ background: true });
    assert.match(readFileSync(new URL('../commonplace/web/static/index.html', import.meta.url), 'utf8'), /href="\/app\/compute\.css"/);
  },
};

// No browser, model, backend, device or network is needed for these assertions.
const original = globalThis.FormData;
globalThis.FormData = class { constructor(form) { this.entries = form.entries; } [Symbol.iterator]() { return this.entries[Symbol.iterator](); } };
try {
  const selection = process.argv[2]; assert.ok(cases[selection], `Unknown Compute UI case: ${selection}`);
  await cases[selection](); console.log(`Compute UI: ${selection} passed`);
} finally { globalThis.FormData = original; }
