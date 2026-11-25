import {escape as e} from './api.js';
import {setupText} from './bot-setup.js';
import {modelStep, machineStep} from './coworker-setup.js';

const titles = {model: 'Model', key: 'Model key', machine: 'Machine or runner', connection: 'Agent connection', trust: 'Machine trust and approval', notifications: 'Notifications'};
// An open step is named as the thing to do.
const todo = {model: 'Choose a model', key: 'Add the model key', machine: 'Add a machine', connection: 'Connect an agent', trust: 'Approve the machine', notifications: 'Notifications'};
const states = {done: 'Done', needs_you: 'Needs you', not_needed: 'Not needed'};
const reasons = {
  model: {done: 'A chat model is chosen and turned on.'},
  key: {done: 'The model key is present and allowed.', not_needed: 'This local model does not require a key.'},
  machine: {done: 'An enabled runner is registered; availability is checked when work starts.', not_needed: 'Personal chat runs here without an agent machine.'},
  connection: {done: 'An agent has used the project connection.', not_needed: 'Personal chat does not need a project agent connection.'},
  trust: {done: 'A runner is approved; project start permissions are granted where enabled.', not_needed: 'Machine start permissions are not in use.'},
  notifications: {not_needed: 'Notifications are optional and never block a start.'},
};

/** Render only server-projected states; no credential or machine identifiers are accepted. */
export function readyToStartHTML(readiness, {canConfigure = false, board = null} = {}) {
  if (!readiness) return '';
  const projectLink = (label, focus = "connection") => board
    ? `<a href="#/board/${encodeURIComponent(board)}" data-action="coordinator" data-board="${e(board)}" data-ready-focus="${focus}">${label}</a>`
    : `<a href="#/settings">${label}</a>`;
  const action = id => ['model', 'key'].includes(id) ? projectLink(board ? 'Project settings' : 'Workspace settings', 'chat')
    : id === 'connection' ? projectLink('Connect agent')
    : id === 'trust' && readiness[id]?.code === 'trust_missing' ? projectLink('Start permissions', 'trust')
    : id === 'notifications' ? '<a href="#/settings">Notification settings</a>'
    : `<a href="#/admin">${id === 'machine' ? 'Add machine' : 'Approve machine'}</a>`;
  const rows = Object.entries(titles).map(([id, title]) => {
    const row = readiness[id] || {}, known = Object.hasOwn(states, row.state), state = known ? row.state : 'needs_you';
    let reason = reasons[id]?.[state];
    const step = state === 'needs_you' && modelStep(row.code);
    if (!reason) reason = id === 'machine' ? machineStep.trim()
      : id === 'connection' ? row.code === 'connection_unused' ? 'The connection exists; open the agent app and use its setup.' : 'Connect an agent to this project.'
      : id === 'trust' ? 'Approve the machine and allow the project connection to start work.'
      : row.code === 'choose_model_first' ? 'Choose a model first so its key requirement can be checked.'
      : setupText(row.code, canConfigure);
    // A step that does not apply has nothing to do, so it gets no action link.
    const link = canConfigure && state !== 'not_needed';
    const copy = step ? `${e(step.before)}${canConfigure ? action(id) : 'the project settings'}${e(step.after)}` : `${e(reason)}${link ? ` ${action(id)}` : ''}`;
    const waits = state === 'needs_you' && row.code === 'choose_model_first';
    return { state, waits, html: `<li data-ready="${id}" data-state="${state}"${waits ? ' data-waits="model"' : ''}><div><b>${state === 'needs_you' ? todo[id] : title}</b><span class="ready-state">${waits ? 'Needs you, after the model' : states[state]}</span></div><p>${copy}${!canConfigure ? ' Ask the project owner to change setup.' : ''}</p></li>` };
  });
  // What needs you comes first; the rows that are fine stay listed, quieter, below it.
  const open = rows.filter(row => row.state === 'needs_you' && !row.waits).length;
  const summary = open ? `${open === 1 ? 'One setup step needs' : `${open} setup steps need`} you before this bot can work.` : 'Everything needed to start is in place.';
  const ordered = [...rows.filter(row => row.state === 'needs_you' && !row.waits), ...rows.filter(row => row.waits), ...rows.filter(row => row.state !== 'needs_you')];
  // The first open step is the next action; the steps that do not apply now are listed last, quietly.
  const html = ordered.map((row, index) => index === 0 && row.state === 'needs_you' ? row.html.replace('data-state="needs_you"', 'data-state="needs_you" data-next') : row.html);
  // Steps that wait on another one, are done or do not apply fold away behind one line, so the steps you can take now
  // and the reply box keep the room.
  const pending = open, rest = html.slice(pending), later = rows.filter(row => row.waits).length;
  const what = later ? (rest.length > later ? 'later, done or not needed' : 'after the model') : 'done or not needed';
  const folded = rest.length ? `<details class="ready-rest"><summary>${rest.length === 1 ? '1 more step' : `${rest.length} more steps`}: ${what}</summary><ul>${rest.join('')}</ul></details>` : '';
  return `<section class="ready-to-start" role="region" aria-label="Ready to start?" data-open="${open}"><h2>Ready to start?</h2><p class="ready-summary">${summary}${open ? ' Start with the first one.' : ''}</p>${pending ? `<ul>${html.slice(0, pending).join('')}</ul>` : ''}${folded}</section>`;
}
