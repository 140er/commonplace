import assert from 'node:assert/strict';
import { runInNewContext } from 'node:vm';

let raw = ''; for await (const part of process.stdin) raw += part;
const sources = JSON.parse(raw);
const url = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return { promise, resolve, reject }; };
const escape = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const documentEvents = new Map();
globalThis.document = {
  addEventListener(k, fn) { if (!documentEvents.has(k)) documentEvents.set(k, new Set()); documentEvents.get(k).add(fn); },
  removeEventListener(k, fn) { documentEvents.get(k)?.delete(fn); },
};
const payload = path => {
  if (path.includes('/settings?')) return path.includes('/workspaces/') ? {limits:{source:'no_account_plan'},items:[]} : {plans_enabled:false,rate_limits_enabled:true,authenticated_requests:600,authenticated_window_seconds:60};
  return {items:path.includes('/people?') ? [{name:'<script>private</script>',email:'person@example.com'}] : path.includes('/workspaces?') ? [{id:'ws_two',name:'Garden'}] : [],count:1};
};

try {
  const { createInstanceAdmin } = await import(url(sources.instance.replace("'./admin.js'", JSON.stringify(url(sources.admin)))));
  function fixture(can = true) {
    const current = {can_manage_instance:can,workspace:'ws_one'}, calls = [], listeners = new Map(), timers = new Map();
    let id = 0;
    const mount = {innerHTML:'',addEventListener(k,fn){listeners.set(k,fn);},removeEventListener(k){listeners.delete(k);}};
    const time = {setTimeout(fn, ms){assert.equal(ms,5000);timers.set(++id,fn);return id;},clearTimeout(id){timers.delete(id);time.onClear?.();}};
    const api = (path, options) => {const d=deferred();calls.push({path,options,...d});return d.promise;};
    const ui = createInstanceAdmin({mount:()=>mount,api,e:escape,current:()=>current,timers:time});
    return {ui,mount,calls,timers,current,listeners,time};
  }
  const f = fixture(); f.ui.show();
  assert.equal(f.calls.length,7);
  for(const call of f.calls) if(!call.path.includes('/connections?')) call.resolve(payload(call.path));
  await flush();
  assert.match(f.mount.innerHTML,/person@example.com/);
  assert.match(f.mount.innerHTML,/&lt;script&gt;/);
  assert.doesNotMatch(f.mount.innerHTML,/<script>/);
  assert.match(f.mount.innerHTML,/Operator setting/);
  assert.match(f.mount.innerHTML,/Loading/);
  for(const fn of [...f.timers.values()]) fn(); await flush();
  assert.match(f.mount.innerHTML,/Metadata unavailable/);
  const count=f.calls.length; f.ui.refreshSection('connections'); await flush();
  assert.equal(f.calls.length,count+1);
  assert.match(f.mount.innerHTML,/person@example.com/,'Retry must preserve other sections');
  f.ui.selectWorkspace('ws_two');
  assert.doesNotMatch(f.mount.innerHTML,/person@example.com/,'Changing target clears metadata');
  f.calls[count].resolve({items:[{name:'stale-response'}],count:1});await flush();
  assert.doesNotMatch(f.mount.innerHTML,/stale-response/,'Late responses cannot repopulate a new target');
  const last = f.calls.at(-1);last.reject({status:403});await flush();
  assert.equal(f.mount.innerHTML,'<p role="status">Instance administrator access required</p>');
  assert.equal(f.timers.size,0);
  assert.ok(f.calls.slice(count+1,-1).every(c=>c.options.signal.aborted));
  const no = fixture(false);no.ui.show();assert.equal(no.calls.length,0);assert.match(no.mount.innerHTML,/Instance administrator access required/);
  const out=fixture();out.ui.show();for (const fn of documentEvents.get('signout')) fn();await flush();assert.equal(out.mount.innerHTML,'');assert.equal(out.timers.size,0);
  no.ui.stop();
  const lost=fixture();lost.ui.show();lost.current.can_manage_instance=false;lost.calls[0].resolve(payload(lost.calls[0].path));await flush();assert.match(lost.mount.innerHTML,/Instance administrator access required/);assert.equal(lost.timers.size,0);
  const guard=fixture();guard.ui.show();
  guard.time.onClear=()=>{delete guard.time.onClear;guard.ui.selectWorkspace('ws_two');};
  guard.calls[0].resolve({items:[{id:'ws_old',name:'stale-response'}],count:1});await flush();
  assert.doesNotMatch(guard.mount.innerHTML,/stale-response/);
  guard.ui.stop();await flush();
  assert.ok(guard.calls.slice(7).every(c=>c.options.signal.aborted),'Stale settlement cannot remove a new target request');

  // Use the production decoder: response headers can settle while JSON never does.
  const { api } = await import(url(sources.api));
  const { boundedRequest } = await import(url(sources.admin));
  globalThis.fetch=async()=>({ok:true,status:200,json:()=>new Promise(()=>{})});
  let fire,cleared=false;
  const stuck=boundedRequest(api,'/synthetic',{timers:{setTimeout(fn){fire=fn;return 1;},clearTimeout(){cleared=true;}}});
  let settled=false;stuck.then(()=>{settled=true;},()=>{settled=true;});
  await flush();fire();await flush();assert.ok(settled,'Body decoding must have a deadline');await assert.rejects(stuck,{name:'TimeoutError'});assert.ok(cleared);
  // Actual navigation renderer and actual route glue, without unrelated data loading.
  const rail = await import(url(sources.rail.replace(/(['"])\.\/api\.js\1/g, JSON.stringify(url(sources.api)))));
  assert.match(rail.renderRail({can_manage_instance:true}),/href="#\/instance-admin"/);
  assert.doesNotMatch(rail.renderRail({can_manage_instance:false}),/href="#\/instance-admin"/);
  // Exercise the mobile command list with only its unrelated ID dependency stubbed.
  const paletteSource = sources.palette.replace(/import .* from .*;\n/g, '');
  const paletteContext = {};
  runInNewContext(paletteSource.replace(/export /g, ''), paletteContext);
  assert.ok(paletteContext.paletteItems({can_manage_instance:true}).some(c=>c.href==='#/instance-admin'));
  assert.ok(!paletteContext.paletteItems({can_manage_instance:false}).some(c=>c.href==='#/instance-admin'));
  const app = sources.app;
  assert.match(app,/paletteUI\(\).open\(\{[^\n]*can_manage_instance: state.me\?\.can_manage_instance === true/);
  assert.match(app,/if \(hash === "#\/instance-admin"\) \{ instanceAdminScreen\(\); return; \}/);
  assert.match(app,/previousBase === "#\/instance-admin"/);
  const route = app.slice(app.indexOf('async function route()'),app.indexOf('// The current view'));
  const content={innerHTML:'',children:[]}, state={me:{can_manage_instance:false},screen:0,base:'#/',route:'#/',boards:[],chatVisible:new Set(),panelRequest:0};
  let shown=0,shared=0;
  const context={remember(){},state,location:{hash:'#/instance-admin'},history:{replaceState(){}},localStorage:{getItem:()=>null},resolveRoute:x=>x,boardPreference:()=>'',decideStart:()=>undefined,clearAgentSecret(){},voice:{stop(){}},speech:{stop(){}},markOpenCard(){},message:x=>x,showError(){},queueFlush(){},queueStop(){},boardDispose:null,consoleUI:null,detailsUI:null,adminUI:null,instanceAdminUI:null,root:{querySelector:k=>k==='#content'?content:k==='#shell-dialog'?{close(){}}:{}},instanceAdminScreen(){shown++;},refresh(){shared++;}};
  runInNewContext(route,context);await context.route();assert.equal(shown,1);assert.equal(shared,0);assert.equal(state.base,'#/instance-admin');
} catch(error) {
  process.stderr.write(error.stack || String(error));
  process.exitCode=error.code==='ERR_ASSERTION' ? 42 : 1;
}
