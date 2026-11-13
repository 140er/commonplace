// Deck presentation only. The shared controller keeps every permission and save route.
const GROUPS = [
  ['Cards and projects', /^(task_|card_|board_|space_|goal_|ask$|decide$|needs_me$)/],
  ['Memory and knowledge', /^(mempalace_|memory_|kg_|fact)/],
  ['Notifications and connections', /notif|webhook|phone|connection|oauth|digest/],
  ['Bots and conversations', /bot|chat|thread|handoff|session|coworker|orchestrator|console|bridge|meeting|^orch_/],
  ['Rules and workspace', /rule|account|workspace|skill|routine|schedule|hold|snooze|costs/],
  ['Machines and sites', /compute|runner|topology|machine|site/],
];
const WORDS = { task:'card', board:'project', space:'project chat', kg:'knowledge',
  orchestrator:'Coworker', coordinator:'Coworker', agent:'Bot', agents:'Bots',
  notif:'notification', ack:'acknowledge', stats:'statistics', ttl:'expiry',
  config:'configuration', auth:'access', own:'dedicated', aaak:'abbreviation', spec:'guide',
  orch:'Coworker', gate:'check', exp:'experiment', diff:'changes', render:'preview',
  void:'cancel', force:'forced', intent:'plan', artifact:'file', peers:'connections' };
const SPECIAL = { ask:'Ask a question', decide:'Answer a decision', needs_me:'Read what needs you',
  orchestrator_inbox:'Read Coworker inbox', session_start:'Start a session',
  webhook_add:'Connect event delivery', webhook_list:'List event connections',
  webhook_remove:'Remove an event connection', webhook_test:'Test event delivery',
  card_start:'Start a card', card_start_status:'Read card start status',
  card_start_resolve:'Resolve a card start', card_start_cancel:'Cancel a card start',
  task_take_next:'Take the next card', task_heartbeat:'Keep card ownership active',
  mempalace_get_taxonomy:'Read memory organization', mempalace_memories_filed_away:'Mark memories as filed',
  mempalace_mesh_peers:'List memory connections', board_own_wing:'Give project a dedicated memory wing',
  space_own_wing:'Give project chat a dedicated memory wing', board_set:'Set project Coworker', space_set:'Set chat Coworker', card_split_done:'Finish splitting a card', card_goal:'Set card goal',
  task_approve:'Approve a card', task_comment:'Comment on a card',
  task_handoff:'Hand off a card', task_snooze:'Snooze a card', task_unsnooze:'End card snooze',
  orch_heartbeat:'Keep Coworker active', orch_exp_retire:'Retire Coworker experiment' };
const VERBS = { add:'Create', create:'Create', list:'List', get:'Read', show:'Read', read:'Read',
  save:'Save', update:'Update', set:'Set', delete:'Delete', remove:'Remove', search:'Search',
  query:'Search', write:'Write', post:'Post', append:'Append', export:'Export', import:'Import',
  start:'Start', cancel:'Cancel', resolve:'Resolve', propose:'Propose', accept:'Accept', reject:'Reject',
  open:'Open', record:'Record', release:'Release', resume:'Resume', acknowledge:'Acknowledge',
  invalidate:'Invalidate', supersede:'Replace', find:'Find', follow:'Follow', put:'Store', submit:'Submit',
  publish:'Publish', test:'Test', select:'Select', discover:'Discover' };
export function permissionLabel(action) {
  if (SPECIAL[action.name]) return SPECIAL[action.name];
  const words = action.name.replace(/^mempalace_/, 'memory_').split(/[_:.\s-]+/).filter(Boolean);
  const verbAt = words.findIndex(word => VERBS[word]);
  const verb = verbAt < 0 ? '' : VERBS[words.splice(verbAt, 1)[0]];
  const text = words.map(word => WORDS[word] || word).join(' ');
  return verb ? `${verb} ${text}` : text.charAt(0).toUpperCase() + text.slice(1);
}
export function permissionGroup(action) {
  return GROUPS.find(([, pattern]) => pattern.test(action.name))?.[0] || 'Other actions';
}
export function permissionRows(actions, modes, filter, e) {
  const needle = filter.trim().toLowerCase();
  const groups = new Map([...GROUPS.map(([label]) => [label, []]), ['Other actions', []]]);
  const visible = actions.filter(action => [permissionLabel(action), permissionGroup(action), action.name]
    .join(' ').toLowerCase().includes(needle));
  visible.sort((a,b) => (a.mode === 'allowed') - (b.mode === 'allowed') || permissionLabel(a).localeCompare(permissionLabel(b)));
  for (const action of visible) groups.get(permissionGroup(action)).push(action);
  const labels = {allowed:'Allowed', ask:'Ask first', off:'Off'};
  return [...groups].filter(([, rows]) => rows.length).map(([group, rows]) =>
    `<section class="deck-permission-group"><h3>${e(group)}</h3>${rows.map(action => {
      const label = permissionLabel(action);
      const kind = {read:'Reads',write:'Changes things',unknown:'No longer served'}[action.kind] || action.kind;
      return `<div class="setting-row" data-tool-policy-row="${e(action.name)}"><div class="deck-permission-label"><span>${e(label)}</span><small>${e(kind)}</small><details><summary>Technical name</summary><code>${e(action.name)}</code></details></div><select data-tool-policy-mode="${e(action.name)}" aria-label="Permission for ${e(label)}">${modes.map(mode => `<option value="${e(mode)}"${mode === action.mode ? ' selected' : ''}>${e(labels[mode] || mode)}</option>`).join('')}</select></div>`;
    }).join('')}</section>`).join('') || '<p class="message">No action matches</p>';
}
export const policyPresentation = {
  label: permissionLabel, rows: permissionRows,
  filterChanged(host) {
    const frame = host.closest?.('.settings'), field = host.querySelector('[data-tool-policy-filter]');
    if (!frame || !field) return;
    const top = field.getBoundingClientRect().top, edge = frame.getBoundingClientRect().top;
    // Shorter results can clamp the scroll position. Keep the search and its results in view.
    if (top < edge || top > edge + frame.clientHeight / 2) frame.scrollBy({ top: top - edge - 12 });
  },
  filterLabel:'Search permissions', placeholder:'Search, e.g. notifications or cards',
  introduction: minutes => `Choose what Bots may do. Allowed uses their existing access. Ask first holds the action in Needs me; approval covers that Bot and that exact call once, for ${minutes} minutes. Off refuses the action. These permissions do not apply to you in the app.`,
};

export function arrangeSettings(box, { owner = false } = {}) {
  // Move existing sections with their forms intact; no setting or event handler is replaced.
  const phone = box.querySelector('#phone-push');
  const connections = box.querySelector('.agents-settings') || [...(box.querySelectorAll?.('.settings-section') || [])]
    .find(section => section.querySelector('h2')?.textContent === 'Agents');
  if (connections) {
    connections.id = 'deck-connections';
    connections.querySelector('h2').textContent = 'Connections';
  }
  const policy = box.querySelector('#tool-policy h2');
  if (policy) policy.textContent = 'Action permissions';
  const unavailable = phone?.querySelector('p');
  if (unavailable?.textContent === 'Not set up on this server.')
    unavailable.textContent = 'Ask the workspace owner to connect a notification service and enable phone notifications on the server. Then return here to turn them on for your phone.';
  const nav = box.querySelector('#deck-settings-nav');
  if (connections) box.prepend?.(connections);
  if (phone) box.prepend?.(phone);
  if (nav) { box.prepend?.(nav); return; }
  box.insertAdjacentHTML('afterbegin', `<nav id="deck-settings-nav" aria-label="Settings sections"><button type="button" data-deck-setting="phone-push">Notifications</button><button type="button" data-deck-setting="deck-connections">Connections</button><button type="button" data-deck-setting="deck-tools">Workspace</button>${owner ? '<button type="button" data-deck-setting="tool-policy">Action permissions</button>' : ''}</nav>`);
}
