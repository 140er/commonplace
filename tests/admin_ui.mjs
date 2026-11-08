import assert from 'node:assert/strict';
import { runInNewContext } from 'node:vm';

let raw = ''; for await (const part of process.stdin) raw += part;
const sources = JSON.parse(raw);
const url = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const apiURL = url(sources.api);
const load = async name => import(url(sources[name].replace(/(['"])\.\/api\.js\1/g, JSON.stringify(apiURL))));
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
const me = {workspace: 'w-one', workspace_name: 'Northwind', can_admin: true, can_admin_instance: false};
const userData = name => ({workspace_id:'w-one', workspace:{id:'w-one',name:'Northwind'}, members:[{id:'u-one',name,email:'person@example.com',role:'owner'}], count:1, has_more:false, next_cursor:null});
const connectionData = {workspace_id:'w-one', connections:[],count:0,has_more:false,next_cursor:null};
function fixture(createAdmin, overrides = {}) {
  let active = {...me};
  const listeners = new Map(), timers = new Map(), requests = [];
  let id = 0;
  const mount = {innerHTML:'', addEventListener(k,fn){listeners.set(k,fn);}, removeEventListener(k){listeners.delete(k);}, querySelector(){return null;}};
  const time = {setTimeout(fn,ms){const key = ++id; timers.set(key,{fn,ms}); return key;},clearTimeout(key){timers.delete(key);}};
  const api = (path,options={}) => {const d = deferred(); requests.push({path,options,...d}); return d.promise;};
  const ui = createAdmin({mount:()=>mount,api,e:escapeHTML,current:()=>active,timers:time,...overrides});
  const expire = () => {for(const [key,{fn,ms}] of [...timers]) {assert.equal(ms,5000); timers.delete(key); fn();}};
  return {ui,mount,timers,requests,time,expire,setMe(value){active=value;},click(key,action='retry'){listeners.get('click')?.({target:{closest:()=>({dataset:{adminSection:key,adminAction:action}})}});}};
}
const escapeHTML = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function section(html,key) {return html.match(new RegExp(`<section[^>]*data-admin-section="${key}"[^>]*>[\\s\\S]*?<\\/section>`))?.[0] || '';}
function appSlice(start,end) {const at=sources.app.indexOf(start), stop=sources.app.indexOf(end,at);assert.ok(at>=0&&stop>at,`Missing production function ${start}`);return sources.app.slice(at,stop);}

async function main() {
  const which = process.argv[2];
  if(which==='navigation') {
    const {renderRail,renderPhoneTabs}=await load('rail');
    assert.equal((renderRail({can_admin:true}).match(/href="#\/admin"/g)||[]).length,1,'Owner account menu must contain one Admin entry');
    for(const can_admin of [false,undefined,'true']) assert.doesNotMatch(renderRail({can_admin}),/href="#\/admin"/);
    assert.doesNotMatch(renderPhoneTabs({can_admin:true}),/href="#\/admin"/,'Keep the existing phone tabs');
    const paletteSource=sources.palette.replace(/import \{ cardId \} from '.\/board.js';/, "const cardId = () => '';");
    const {paletteItems}=await import(url(paletteSource.replace(/(['"])\.\/api\.js\1/g,JSON.stringify(apiURL))));
    assert.equal(paletteItems({can_admin:true}).filter(x=>x.href==='#/admin').length,1);
    assert.equal(paletteItems({can_admin:false}).filter(x=>x.href==='#/admin').length,0);
    return;
  }
  const {createAdmin,boundedRequest}=await load('admin');
  globalThis.document = new EventTarget();
  if(which==='route') {
    const {resolveRoute}=await load('routes');
    for(const can_admin of [true,false]) {
      // Execute the production route and refresh functions; unrelated sources never settle.
      const f=fixture(createAdmin); f.setMe({...me,can_admin});
      const state={me:{...me,can_admin},screen:0,route:'#/',base:'#/',boards:[],needs:{},cards:[],drafts:new Map(),pending:new Set(),chatVisible:new Set(),refreshRequest:0,panelRequest:0};
      const nodes={'#content':f.mount,'#card-panel':{hidden:true},'#shell-dialog':{close(){}}};
      f.mount.children=[];
      let shared=0;
      const context={state,root:{querySelector:s=>nodes[s]||null},location:{hash:'#/admin'},history:{replaceState(){}},localStorage:{getItem:()=>null},resolveRoute,boardPreference:()=>'',decideStart:()=>undefined,clearAgentSecret(){},voice:{stop(){}},speech:{stop(){}},remember(){},markOpenCard(){},message:x=>x,showError(){},queueFlush(){},queueStop(){},boardDispose:null,consoleUI:null,detailsUI:null,adminUI:f.ui,createAdmin,api(){shared++;return new Promise(()=>{});},e:escapeHTML,title(){},navigation(){},currentBoard:()=>null,consoleScreen(){}};
      // adminScreen is production shell glue; use the real controller with the same mount.
      runInNewContext(appSlice('function adminScreen(', 'function consoleScreen(')+appSlice('async function refresh(', 'function decideStart(')+appSlice('async function route()', '// The current view'),context);
      context.adminUI=f.ui;
      void context.route(); await flush();
      assert.equal(state.base,'#/admin','Admin must survive the hash allowlist');
      assert.equal(shared,0,'Admin must bypass shared boards/needs/rules fan-in');
      if(can_admin) {assert.match(f.mount.innerHTML,/Users and workspaces/);assert.equal(f.requests.length,2);}
      else {assert.match(f.mount.innerHTML,/a signed-in workspace owner is required/);assert.equal(f.requests.length,0);}
      const calls=f.requests.length;
      const background=context.refresh({background:true,events:[{kind:'changed'}]});await flush();
      assert.equal(shared,0,'Background events must not enter the shared fan-in');await background;
      assert.equal(f.requests.length,calls,'Event refresh must not remount or reload Admin');
      assert.equal(shared,0);
      f.ui.stop();
    }
  } else if(which==='independent') {
    const f=fixture(createAdmin); f.ui.show();
    assert.equal(f.requests.length,2);
    assert.ok(f.requests.every(r=>!r.options.method||r.options.method==='GET'));
    const users=f.requests.find(r=>r.path.endsWith('/users'));
    users.resolve(userData('<img src=x onerror=bad()>')); await flush();
    assert.match(section(f.mount.innerHTML,'users'),/&lt;img/);
    assert.doesNotMatch(f.mount.innerHTML,/<img/);
    assert.match(section(f.mount.innerHTML,'connections'),/Loading/);
    f.expire(); await flush();
    assert.match(section(f.mount.innerHTML,'connections'),/Connections timed out\. Retry\./);
    assert.match(section(f.mount.innerHTML,'connections'),/aria-label="Retry Connections"/);
    assert.match(section(f.mount.innerHTML,'users'),/person@example.com/);
    assert.equal(f.timers.size,0);
    for(const key of ['runtime','limits','stuck','deploy','backups']) assert.match(section(f.mount.innerHTML,key),/Available in a later slice/);
    assert.ok(f.requests.every(r=>/\/(users|connections)$/.test(r.path)));
    f.ui.stop();
  } else if(which==='lifecycle') {
    const f=fixture(createAdmin); f.ui.show();
    f.requests[0].resolve(userData('First')); f.requests[1].reject(new Error('source canary')); await flush();
    assert.match(section(f.mount.innerHTML,'connections'),/Connections unavailable\. Retry\./);
    assert.doesNotMatch(f.mount.innerHTML,/source canary/);
    f.click('connections'); await flush();
    assert.equal(f.requests.length,3); assert.ok(f.requests[2].path.endsWith('/connections'));
    f.click('connections'); assert.equal(f.requests.length,3,'Only one active request per key');
    f.requests[2].resolve(connectionData);await flush();
    f.ui.refreshSection('users'); await flush();
    assert.match(section(f.mount.innerHTML,'users'),/First/);assert.match(section(f.mount.innerHTML,'users'),/Refreshing/);
    const old=f.requests.at(-1); f.expire();await flush();
    f.ui.refreshSection('users');await flush();const recent=f.requests.at(-1);
    recent.resolve(userData('Newest'));await flush();old.resolve(userData('STALE'));await flush();
    assert.match(f.mount.innerHTML,/Newest/);assert.doesNotMatch(f.mount.innerHTML,/STALE/);
    f.ui.refreshSection('users');const pending=f.requests.at(-1);f.ui.stop();pending.resolve(userData('LATE'));await flush();
    assert.equal(f.mount.innerHTML,'');assert.equal(f.timers.size,0);assert.equal(pending.options.signal.aborted,true);
    f.ui.show(); f.setMe({...me,workspace:'w-two'}); f.requests.at(-2).resolve(userData('FOREIGN')); await flush();
    assert.doesNotMatch(f.mount.innerHTML,/FOREIGN|Newest/);assert.equal(f.timers.size,0);
    f.setMe(me);f.ui.show();f.requests.at(-2).reject(Object.assign(new Error('expired'),{status:401}));await flush();
    assert.equal(f.mount.innerHTML,'');assert.equal(f.timers.size,0);
    f.ui.show();document.dispatchEvent(new Event('signout'));await flush();assert.equal(f.mount.innerHTML,'');assert.equal(f.timers.size,0);
    // Use the real api: response headers arrive, but response.json never resolves.
    const apiModule=await import(apiURL), body=deferred();
    globalThis.fetch=async()=>({status:200,ok:true,json:()=>body.promise});
    let rejected=false;
    const bootstrap=boundedRequest(apiModule.api,'/api/me',{timers:f.time}).catch(err=>{rejected=err.name==='TimeoutError';});
    await flush();f.expire();await flush();
    assert.equal(rejected,true,'Body decode must be bounded too');await bootstrap;assert.equal(f.timers.size,0);
    assert.match(sources.app,/boundedRequest\(api,\s*["']\/api\/me["']/,'Bootstrap must use the bounded primitive');
    assert.match(sources.app,/adminUI\?\.stop\(\)/,'Shell must dispose Admin');
    f.ui.stop();
    // Resolve a response, let Promise.race settle, then replace the page before
    // its async continuation can render. A transport abort alone cannot fence it.
    const race=fixture(createAdmin);race.ui.show();
    race.requests[0].resolve(userData('OLD PAGE'));
    await Promise.resolve();
    race.ui.stop();race.ui.show();await flush();
    assert.match(section(race.mount.innerHTML,'users'),/Loading/,'A completed old-page continuation must not consume the new request state');
    assert.equal(race.timers.size,2);race.ui.stop();
  } else assert.fail('Unknown case');
}
try { await main(); } catch(error) {
  if(error.code==='ERR_ASSERTION') {console.error(error.stack);process.exitCode=42;}
  else {console.error(error.stack);process.exitCode=2;}
}
