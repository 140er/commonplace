import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { policyState, policyValue, capyPermissionRows, capyPolicyPresentation, settingsOrder, settingsLayout, scopeHTML, connectionLabel } from '../commonplace/web/static/deck-settings-capy.js';
import { mountToolPolicy } from '../commonplace/web/static/tool-policy.js';
import { escape as e } from '../commonplace/web/static/api.js';
const modes = JSON.parse(process.env.POLICY_MODES || '["allowed","ask","off"]');
const names = JSON.parse(process.env.POLICY_ACTIONS || '["shell.exec","git.push","deploy.test","memory_save","task_add"]');

test('all server policy values round trip through the three labels without normalization', () => {
  for (const value of [...modes, 'ask_first', 'approval_required', 'deny', 'custom-restricted', '__proto__']) {
    const state = policyState(value);
    assert.ok(['allow','ask','deny'].includes(state));
    assert.equal(policyValue(state, value, modes), value);
    const html = capyPermissionRows([{name:'task_add',kind:'write',mode:value}], modes, '', e);
    assert.ok(html.includes(`value="${e(value)}" selected`), value);
    assert.match(html, new RegExp(`data-policy-state="${state}" aria-pressed="true"`));
  }
  assert.equal(policyValue('deny', 'allowed', modes), 'off');
  assert.equal(policyValue('allow', 'off', modes), 'allowed');
  assert.equal(policyValue('ask', 'off', modes), 'ask');
  assert.equal(policyValue('allow','custom',['custom']), null);
});

test('every installed entry stays searchable by human label and exact wire identity', () => {
  const actions = names.map(name => ({name,kind:'write',mode:'allowed'}));
  const html = capyPermissionRows(actions,modes,'',e);
  assert.equal((html.match(/data-tool-policy-row=/g) || []).length, names.length);
  for (const name of names) {
    const label = capyPolicyPresentation.label({name});
    for (const filter of [name,label]) assert.ok(capyPermissionRows(actions,modes,filter,e).includes(`data-tool-policy-row="${e(name)}"`), `${name} via ${filter}`);
    const row = capyPermissionRows([{name,mode:'allowed',kind:'write'}], modes,'',e);
    assert.equal((row.match(/data-policy-state=/g) || []).length,3);
    assert.ok(!row.replace(/<details[\s\S]*?<\/details>/g,'').replace(/<[^>]+>/g,'').includes(name), name);
  }
});

test('segmented click uses the existing save route, preserves identity and ignores unchanged state', async () => {
  const calls=[], actions=[{name:'memory_save',kind:'write',mode:'approval_required'}];
  const select={value:'approval_required',dataset:{toolPolicyMode:'memory_save'},disabled:false,
    dispatchEvent: event => list.onchange({target:{closest:()=>select}})};
  const group={querySelector:()=>select,querySelectorAll:()=>[]};
  const button={disabled:false,dataset:{policyState:'ask'},closest:()=>group};
  const list={}, input={};
  const host={isConnected:true,querySelector:q=>q==='[data-tool-policy-list]'?list:input};
  mountToolPolicy(host,{e,presentation:capyPolicyPresentation,api:async(path,options)=>{
    calls.push([path,options]);return {actions,modes,approval:{approval_ttl_s:600}};
  }});
  await new Promise(resolve=>setImmediate(resolve));
  list.onclick({target:{closest:()=>button}});
  assert.equal(calls.length,1,'selected approximation is a no-op');
  button.dataset.policyState='deny';
  list.onclick({target:{closest:()=>button}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls[1],['/api/tool-policy',{method:'POST',body:{actions:{memory_save:'off'}}}]);
});

test('layout keeps every old section, places the four primary sections first and stacks Appearance last', () => {
  const old=['phone-push','deck-connections','deck-tools','plan','voice','chat','agent-schedules','tool-policy','webhooks','sign-out','look'];
  const sections=[...old,'deck-bot-sharing','deck-second-review'].map(id=>({id}));
  const ordered=settingsOrder(sections);
  assert.deepEqual(ordered.slice(0,4).map(n=>n.id),['deck-bot-sharing','tool-policy','deck-second-review','deck-connections']);
  assert.deepEqual(new Set(ordered),new Set(sections));
  assert.equal(ordered.length,sections.length);
  assert.equal(settingsLayout(1440),'columns'); assert.equal(settingsLayout(390),'stack');
  const css=readFileSync(new URL('../commonplace/web/static/deck-settings-capy.css',import.meta.url),'utf8');
  assert.match(css, /@media \(max-width: 1099px\)/);
  assert.match(css, /grid-template-columns: minmax\(0,1fr\)/);
  assert.match(css, /--red/); assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b/i);
});

test('scope labels lead with human words and preserve the grey wire suffix', () => {
  assert.match(scopeHTML('events:person'), /Your activity <code>events:person<\/code>/);
  assert.ok(scopeHTML('<img>').includes('&lt;img&gt;'));
  for(const client of ['claude-code','codex','cursor','generic','new-client']) assert.ok(!/claude|codex|cursor|grok/i.test(connectionLabel(client)));
});

test('the arrangement moves all legacy setting nodes intact, including newly added sections', async () => {
  const { arrangeCapySettings } = await import('../commonplace/web/static/deck-settings-capy.js');
  class Node {
    constructor(id = '') { this.id=id; this.children=[]; this.classList={add(){}}; this.heading={textContent:''}; }
    append(node) { node.parent?.children.splice(node.parent.children.indexOf(node),1); node.parent=this; this.children.push(node); }
    prepend(node) { this.append(node); this.children.unshift(this.children.pop()); }
    querySelector(selector) {
      if(selector==='h2') return this.heading;
      if(selector==='[data-capy-sharing]') return {textContent:''};
      const id=selector.match(/^#([^ ]+)/)?.[1], node=this.children.find(item=>item.id===id);
      return selector.endsWith(' h2') ? node?.heading : node || null;
    }
    setAttribute() {}
  }
  const app=readFileSync(new URL('../commonplace/web/static/app.js',import.meta.url),'utf8');
  const source=app.slice(app.indexOf('async function settingsView('),app.indexOf('async function memoryView('));
  // Enumerate the real Settings render's named mounts, forms, draft fields and actions.
  const entries=[...new Set([...source.matchAll(/(?:data-(?:form|action|draft)|id|data-(?:workspace-orchestration|agent-schedules|tool-policy))="([^"]*)"/g)].map(match=>match[0]))];
  for (const required of ['data-form="webhook"', 'data-action="logout"', 'id="tool-policy"', 'data-draft="webhook-url"']) assert.ok(entries.includes(required), required);
  const main=new Node(), box={ownerDocument:{createElement:()=>new Node()},classList:{add(){}},querySelector:()=>main};
  for(const id of ['deck-connections','tool-policy','phone-push','deck-tools','look','deck-settings-nav',...entries]) main.append(new Node(id));
  const before=[...main.children];
  arrangeCapySettings(box);
  for(const entry of before) assert.ok(main.children.includes(entry),entry.id);
  assert.equal(new Set(main.children).size,main.children.length,'each section is reachable once');
  const first=main.children.slice(0,4).map(node=>node.id);
  assert.deepEqual(first,['deck-bot-sharing','tool-policy','deck-second-review','deck-connections']);
  arrangeCapySettings(box);
  assert.deepEqual(main.children.slice(0,4).map(node=>node.id),first,'late refresh is idempotent');
});

test('connected app copy and scope titles are plain while classic retains its client labels', async () => {
  const { agentsView } = await import('../commonplace/web/static/agents.js');
  const payload={connections:[{id:'app-1',name:'My app',client:'codex',auth:'oauth',scopes:['events:person'],bots_chat:true,bots_confirm:true,decide:true}]};
  const previous=globalThis.document;
  try {
    globalThis.document={documentElement:{dataset:{ui:'deck'}}};
    const html=agentsView(payload);
    const visible=html.replace(/<[^>]+>/g,'');
    assert.doesNotMatch(visible,/Claude|Codex|Cursor|Grok/);
    assert.match(html,/Your activity <code>events:person<\/code>/);
    assert.match(html,/Answer your questions <code>cards:decide<\/code>/);
    globalThis.document.documentElement.dataset={};
    const classic=agentsView(payload);
    assert.match(classic,/>Claude Code<\/option>/);
    assert.match(classic,/Uses your bots \(bots:chat, bots:confirm\)/);
  } finally { globalThis.document=previous; }
});
