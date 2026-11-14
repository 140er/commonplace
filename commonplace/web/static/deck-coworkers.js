// Capy Bot lists and Coworker detail. Optional projections never invent work.
import { escape as e } from './api.js';
import { workspaceRoute } from './routes.js';

const enc = encodeURIComponent;
const list = value => Array.isArray(value) ? value : [];
const positive = value => Number.isSafeInteger(value) && value > 0 ? value : 0;
const requestId = () => globalThis.crypto?.randomUUID?.() || `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 14)}`;
const present = (objects, key) => objects.find(value => value && Object.hasOwn(value, key));
// This is presentation copy only; request bodies always preserve the exact draft.
export const displayCopy = value => String(value ?? '').replace(/\b(?:PR|card|ticket)\s*#\d+\s*[:·-]?\s*/gi, '').replace(/#\d+\b/g, '').replace(/(?:~\/|\/(?:home|Users|var|tmp)\/)[^\s,;]+/g, '').replace(/\s+/g, ' ').trim();
const name = value => displayCopy(value) || 'Coworker';
const avatar = value => `<span class="cb-avatar" aria-hidden="true">${e(Array.from(name(value))[0])}</span>`;
const lock = label => `<svg class="cb-lock" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" role="img" aria-label="${label}"><rect x="4" y="7" width="8" height="6" rx="1.5"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/></svg>`;
export function stateView(state) {
  if (['needs_you', 'needs_person', 'waiting_on_owner'].includes(state)) return { tone: 'waiting', word: 'Waiting on you' };
  if (['working', 'running', 'started', 'busy', 'pending', 'queued', 'building'].includes(state)) return { tone: 'working', word: ['pending', 'queued'].includes(state) ? 'Queued' : 'Working' };
  if (['done', 'completed', 'succeeded', 'ready', 'healthy', 'live', 'connected'].includes(state)) return { tone: 'live', word: state === 'ready' ? 'Ready' : state === 'healthy' ? 'Healthy' : state === 'connected' ? 'Connected' : 'Done' };
  return { tone: 'idle', word: state === 'failed' ? 'Failed' : state === 'blocked' ? 'Blocked' : state === 'waiting' ? 'Waiting' : 'Idle' };
}
const dot = state => { const view = stateView(state); return `<span class="cb-dot" data-tone="${view.tone}" role="img" aria-label="${view.word}"></span>`; };
const belongs = (conversation, bot) => list(conversation.bot_ids).some(id => String(id) === String(bot.id)) || Boolean(bot.board && conversation.board === bot.board);

export function overviewBots(bots = [], coworkers = []) {
  const rows = new Map(bots.map(bot => [String(bot.id), { ...bot }]));
  for (const bot of coworkers) {
    const existing = rows.get(String(bot.id));
    if (!existing) rows.set(String(bot.id), { ...bot, coworkerOnly: true });
    else for (const key of ['section', 'group', 'private', 'is_private', 'unread_count', 'unread', 'state', 'summary', 'coworkers', 'coworker_count']) {
      if (Object.hasOwn(bot, key)) existing[key] = bot[key];
    }
  }
  return [...rows.values()];
}

export function botGroups(bots) {
  const groups = new Map();
  for (const bot of bots) {
    const section = typeof bot.section === 'object' ? bot.section?.name : bot.section;
    const label = displayCopy(section || bot.group) || 'Bots';
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(bot);
  }
  return [...groups].map(([label, rows]) => ({ label, bots: rows }));
}
export function capyRosterHTML({ bots = [], conversations = [], loaded = true }) {
  if (!bots.length) return `<li class="bots-note">${loaded ? 'No bots yet. Create one to get started.' : 'Loading…'}</li>`;
  return botGroups(bots).map(group => `<li class="cb-folder"><h2>${e(group.label)}</h2><ul>${group.bots.map(bot => {
    const coworkers = Array.isArray(bot.coworkers) ? bot.coworkers.length : Number.isSafeInteger(bot.coworker_count) ? bot.coworker_count : conversations.filter(row => belongs(row, bot)).length || (bot.board ? 1 : 0);
    const unread = positive(bot.unread_count ?? bot.unread);
    const privateBot = Object.hasOwn(bot, 'private') ? bot.private === true : bot.is_private === true;
    return `<li><a class="cb-bot-row" href="#/bots/${enc(bot.id)}/coworkers">${avatar(bot.name)}<span class="cb-bot-main"><span class="cb-bot-name"><b>${e(name(bot.name))}</b>${dot(bot.state)}${privateBot ? lock('Private bot') : ''}</span><span class="cb-summary">${e(displayCopy(bot.last?.text || bot.summary || bot.job))}</span></span><span class="cb-bot-count">${coworkers} ${coworkers === 1 ? 'coworker' : 'coworkers'}</span>${unread ? `<span class="cb-unread">${unread}<span class="sr"> unread</span></span>` : ''}</a></li>`;
  }).join('')}</ul></li>`).join('');
}

export function workingSteps(worker = {}, detail = {}, checkpoint = {}) {
  const source = present([detail.conversation, detail, worker], 'working_on');
  if (source) {
    const tree = source.working_on;
    return list(Array.isArray(tree) ? tree : tree?.name || tree?.subagent ? [tree] : tree?.steps || tree?.children);
  }
  const steps = ['open', 'needs_person', 'in_doubt', 'finished'].flatMap(key => list(checkpoint[key]));
  if (steps.length) return steps.map(step => ({ name: step.name || step.kind || 'Coworker', summary: step.summary || step.detail, state: step.state, children: step.children }));
  return list(detail.jobs).filter(job => job.task_progress?.availability === 'available').map(job => ({ name: 'Coworker', summary: job.task_progress.status_line || job.task_progress.title, state: job.task_progress.status === 'review' ? 'idle' : job.task_progress.status === 'doing' ? 'working' : job.task_progress.status }));
}
export function ownedPaths(worker = {}, detail = {}) {
  const source = present([detail.conversation, detail, worker], 'owns');
  return source ? list(Array.isArray(source.owns) ? source.owns : source.owns?.paths) : null;
}
export function liveLog(worker = {}, detail = {}) {
  const source = present([detail.conversation, detail, worker], 'live_log');
  if (!source || source.live_log == null) return null;
  const log = source.live_log;
  const tail = typeof log === 'string' || Array.isArray(log) ? log : log.tail ?? log.lines;
  if (typeof tail === 'string') return tail;
  if (Array.isArray(tail)) return tail.map(row => typeof row === 'string' ? row : row.text ?? row.message ?? '').join('\n');
  return null;
}
export function workerModel(worker = {}, detail = {}, project = {}) {
  const model = detail.conversation?.model || detail.model || worker.model || project.models?.main;
  return typeof model === 'string' ? model : model?.label || model?.name || model?.model || '';
}
const stepHTML = (steps, depth = 0) => depth > 12 ? '' : `<ul class="cb-tree">${steps.map(step => `<li><div><b>${e(name(step.name || step.subagent || step.assignee))}</b><span class="cb-step-summary">${e(displayCopy(step.summary || step.action_summary || step.action || step.detail))}</span><span class="cb-state" data-tone="${stateView(step.state).tone}">${dot(step.state)}${e(stateView(step.state).word)}</span></div>${list(step.children).length ? stepHTML(step.children, depth + 1) : ''}</li>`).join('')}</ul>`;
export function panelsHTML(worker, detail = {}, checkpoint = {}) {
  const steps = workingSteps(worker, detail, checkpoint), owns = ownedPaths(worker, detail), log = liveLog(worker, detail);
  const paths = (owns || []).filter(row => typeof row === 'string' || typeof (row.path || row.glob) === 'string');
  return `<div class="cb-panels"><section class="cb-panel"><h3>Working on</h3>${steps.length ? stepHTML(steps) : '<p class="cb-empty">No work recorded yet.</p>'}</section>${paths.length ? `<section class="cb-panel"><h3>Owns <small>${paths.length} ${paths.length === 1 ? 'file' : 'files'}</small></h3><ul class="cb-paths">${paths.map(row => `<li><code>${e(typeof row === 'string' ? row : row.path || row.glob)}</code>${row.shared || row.shared_lock || row.lock?.shared ? `<span class="cb-shared">${lock('Shared lock')}<span>shared</span></span>` : ''}</li>`).join('')}</ul></section>` : ''}</div>${log !== null ? '<section class="cb-panel cb-log"><h3>Live log</h3><pre data-cb-log tabindex="0" aria-label="Live log"></pre></section>' : ''}`;
}
export function renderLiveLog(node, text) {
  if (!node || text === null) return;
  node.textContent = text;
  node.scrollTop = node.scrollHeight;
}
const steerJob = detail => list(detail.jobs).find(job => String(job.conversation_id) === String(detail.conversation?.id) && job.task_progress?.availability === 'available' && job.task_progress.controls?.can_follow_up === true);
export const canSteer = detail => Boolean(steerJob(detail) || detail.conversation?.composer?.unified === true && list(detail.conversation.composer.modes).includes('steer'));
export function composerRequest(detail, mode, text, requestId) {
  const conversation = detail.conversation;
  if (!conversation || !text.trim() || !['steer', 'chat'].includes(mode)) return null;
  const body = { text, request_id: requestId };
  if (conversation.composer?.unified === true && list(conversation.composer.modes).includes(mode)) return { path: `/api/coworkers/conversations/${enc(conversation.id)}/messages`, body: { ...body, mode } };
  if (mode === 'steer') {
    const job = steerJob(detail);
    return job ? { path: `/api/coworkers/jobs/${enc(job.id)}/controls`, body: { ...body, operation: 'follow_up' } } : null;
  }
  // Preserve group addressing: a group message never silently selects a bot.
  if (list(conversation.bot_ids).length === 1) body.bot_id = conversation.bot_ids[0];
  return { path: `/api/coworkers/conversations/${enc(conversation.id)}/messages`, body };
}
export function composerHTML(detail = {}, { mode = 'chat', text = '', busy = false, pending = false } = {}) {
  const conversation = detail.conversation, enabled = Boolean(conversation), steer = canSteer(detail);
  return `<form class="cb-composer" data-cb-compose><label class="sr" for="cb-draft">${mode === 'steer' ? 'Steer Coworker' : 'Chat with Coworker'}</label><textarea id="cb-draft" rows="2" maxlength="8000" placeholder="${mode === 'steer' ? 'Steer this Coworker' : 'Chat with this Coworker'}"${!enabled || busy || pending ? ' disabled' : ''}>${e(text)}</textarea><div class="cb-compose-bar"><div class="cb-modes" role="group" aria-label="Composer mode">${['steer', 'chat'].map(value => `<button type="button" data-cb-mode="${value}" aria-pressed="${mode === value}"${busy || pending || value === 'steer' && !steer ? ' disabled' : ''}>${value === 'steer' ? 'Steer' : 'Chat'}</button>`).join('')}</div><span class="cb-compose-hint">${pending ? 'Send unconfirmed. Retry uses the same request.' : mode === 'steer' ? 'Follow up on the linked task' : conversation?.kind === 'group' ? 'Message the group' : 'Private conversation'}</span><button type="submit" class="cb-send"${!enabled || busy || !text.trim() || mode === 'steer' && !steer ? ' disabled' : ''}>${busy ? 'Sending…' : pending ? 'Retry send' : 'Send'}</button></div>${!enabled ? '<a href="#/bots/-/coworkers/conversations">Open a conversation to send a message</a>' : ''}</form>`;
}

/** Deck-only, while the classic workspace and the conversation controller keep their routes. */
export function mount(context) {
  let ctx = context, stopped = false, epoch = 0, controller = null, selected = null, workers = [], parent = null;
  let detail = {}, project = {}, checkpoint = {}, mode = 'chat', text = '', busy = false, notice = '', attempt = null, refreshing = false;
  const root = ctx.root, doc = root.ownerDocument || globalThis.document, page = workspaceRoute(ctx.route);
  const identity = `${ctx.me?.workspace}:${ctx.me?.actor}`;
  const timers = ctx.timers || globalThis;
  let timer = null;
  const current = () => !stopped && `${ctx.me?.workspace}:${ctx.me?.actor}` === identity;
  root.classList.add('capy-coworkers');
  root.innerHTML = '<div data-cb="body"><p role="status">Loading Coworkers…</p></div><div class="cb-sharing-root" data-cb="sharing"></div>';
  const body = () => root.querySelector('[data-cb="body"]');
  const header = root.parentElement?.querySelector('.head'), wasHidden = header?.hidden;
  if (header) header.hidden = true;
  let sharing = null;
  async function openSharing() {
    const { createBots } = await import('./bots.js');
    if (!current() || !parent) return;
    sharing ||= createBots({ mount: () => root.querySelector('[data-cb="sharing"]'), api: (...args) => ctx.api(...args), e, viewport: null });
    await sharing.showSharing(parent);
  }
  const options = () => ({ signal: controller?.signal, timeoutMs: 10000 });
  const activeWorker = () => workers.find(row => row.key === selected);
  const draftKey = row => `${row?.key}:${mode}`;
  const drafts = new Map();
  function schedule() {
    timers.clearTimeout(timer);
    if (current()) { timer = timers.setTimeout(() => { if (!doc?.hidden) void refresh(); else schedule(); }, 5000); timer?.unref?.(); }
  }
  function visible() { if (!doc?.hidden) void refresh(); }
  function paint() {
    if (!current() || !body()) return;
    const worker = activeWorker(), botName = parent ? name(parent.name) : 'Coworkers';
    const focused = doc?.activeElement?.id === 'cb-draft', selection = focused ? [doc.activeElement.selectionStart, doc.activeElement.selectionEnd] : null;
    const summary = displayCopy(detail.conversation?.summary || worker?.summary), model = displayCopy(workerModel(worker, detail, project));
    body().innerHTML = `<nav class="cb-crumbs" aria-label="Breadcrumb"><a href="#/bots">Bots</a><span>›</span><span>${e(displayCopy(parent?.section?.name || parent?.section) || botName)}</span></nav><header class="cb-heading">${parent ? avatar(parent.name) : ''}<div><h1>${e(botName)}</h1><p>${e(displayCopy(parent?.job) || 'Coworkers and their recorded work')}</p></div></header><nav class="cb-tabs" aria-label="Bot views"><a href="${page.bot ? `#/bots/${enc(page.bot)}/coworkers` : '#/bots/-/coworkers'}" aria-current="page">Coworkers <span>${workers.length}</span></a>${parent && !parent.coworkerOnly ? `<a href="#/bots/${enc(parent.id)}">Conversation</a><button type="button" data-cb-share>Shared</button>` : ''}<a href="#/bots/-/coworkers/conversations">Open conversation</a></nav><div class="cb-layout"><aside class="cb-worker-list" aria-label="Coworkers">${workers.map(row => `<button type="button" class="cb-worker" data-cb-worker="${e(row.key)}"${row.key === selected ? ' aria-current="true"' : ''}>${avatar(row.name)}<span><b>${e(name(row.name))} ${dot(row.state)}</b><small>${e(displayCopy(row.summary) || 'No summary recorded')}</small></span></button>`).join('')}${!workers.length ? '<p class="cb-empty">No Coworkers recorded yet.</p>' : ''}<a class="cb-new-worker" href="#/bots/-/coworkers/conversations"><span aria-hidden="true">+</span><span>New coworker<small>Give it a goal and the files it owns</small></span></a></aside><section class="cb-detail" aria-label="Coworker detail">${worker ? `<header class="cb-detail-head"><h2>${e(name(worker.name))}</h2><p>${e(model ? `Runs on ${model}${summary ? ' · ' : ''}` : '')}${e(summary)}</p></header>${panelsHTML(worker, detail, checkpoint)}` : ''}<p class="cb-notice" role="status">${e(notice)}</p><div data-cb-composer>${composerHTML(detail, { mode, text, busy, pending: Boolean(attempt) })}</div></section></div>`;
    renderLiveLog(root.querySelector('[data-cb-log]'), liveLog(worker, detail));
    if (focused) { const field = root.querySelector('#cb-draft'); field?.focus({ preventScroll: true }); if (selection) field?.setSelectionRange(...selection); }
  }
  async function readWorker(row, mine) {
    detail = {}; project = {}; checkpoint = {};
    if (row?.conversation) {
      const data = await ctx.api(`/api/coworkers/conversations/${enc(row.conversation.id)}`, options());
      if (!current() || mine !== epoch) return;
      if (String(data.conversation?.id) !== String(row.conversation.id)) throw new Error('mismatched conversation');
      detail = data;
    }
    const board = row?.board;
    if (board) {
      const reads = await Promise.allSettled([ctx.api(`/api/projects/${enc(board)}`, options()), ctx.api(`/api/orch/boards/${enc(board)}/roles/main/checkpoint`, options())]);
      if (!current() || mine !== epoch) return;
      project = reads[0].status === 'fulfilled' ? reads[0].value : {};
      checkpoint = reads[1].status === 'fulfilled' ? reads[1].value : {};
    }
    if (!current() || mine !== epoch) return;
    if (mode === 'steer' && !canSteer(detail)) mode = 'chat';
    text = drafts.get(draftKey(row)) || ''; paint();
  }
  async function refresh(next = {}) {
    ctx = { ...ctx, ...next };
    if (!current()) { stop(); return; }
    if (busy || attempt || refreshing) return;
    refreshing = true;
    const mine = ++epoch; controller?.abort(); controller = new AbortController();
    try {
      const [roster, directory] = await Promise.all([ctx.api('/api/bots', options()), ctx.api('/api/coworkers', options())]);
      if (!current() || mine !== epoch) return;
      const bots = overviewBots(list(roster.bots), list(directory.bots));
      parent = bots.find(bot => String(bot.id) === String(page.bot)) || null;
      const conversations = list(directory.conversations).filter(row => !page.bot || parent && belongs(row, parent));
      workers = conversations.map(row => ({ ...row, key: `conversation:${row.id}`, conversation: row, name: row.title || 'Coworker', summary: row.summary || row.last?.text || '', board: row.board, state: row.state || 'idle' }));
      // Older hubs only recorded a project's main Coworker and checkpoint.
      for (const bot of bots.filter(bot => bot.board && (!page.bot || String(bot.id) === String(page.bot)))) {
        if (!workers.some(row => row.board === bot.board)) workers.push({ key: `project:${bot.board}`, board: bot.board, name: `${(ctx.boards || []).find(board => board.slug === bot.board)?.name || name(bot.name)} Coworker`, summary: bot.summary || bot.job, state: bot.state });
      }
      if (!workers.some(row => row.key === selected)) selected = workers[0]?.key || null;
      paint(); await readWorker(activeWorker(), mine);
    } catch (error) {
      if (!current() || mine !== epoch || error.name === 'AbortError') return;
      detail = {}; project = {}; checkpoint = {}; notice = 'Coworkers could not be loaded. Reopen this page to try again.'; paint();
    } finally { refreshing = false; schedule(); }
  }
  async function choose(key) {
    if (busy || attempt || !workers.some(row => row.key === key) || key === selected) return;
    drafts.set(draftKey(activeWorker()), text);
    selected = key; const mine = ++epoch; controller?.abort(); controller = new AbortController();
    detail = {}; text = ''; notice = ''; paint();
    try { await readWorker(activeWorker(), mine); }
    catch (error) { if (current() && mine === epoch && error.name !== 'AbortError') { detail = {}; checkpoint = {}; notice = 'This Coworker is unavailable.'; paint(); } }
  }
  function chooseMode(value) {
    if (busy || attempt || !['chat', 'steer'].includes(value) || value === 'steer' && !canSteer(detail)) return;
    drafts.set(draftKey(activeWorker()), text); mode = value; text = drafts.get(draftKey(activeWorker())) || ''; paint();
  }
  async function send() {
    if (!current() || busy) return;
    const request = attempt || composerRequest(detail, mode, text, requestId());
    if (!request) return;
    const mine = epoch, id = detail.conversation.id;
    attempt = request; busy = true; notice = ''; paint();
    try {
      const result = await ctx.api(request.path, { ...options(), method: 'POST', body: request.body });
      if (!current() || mine !== epoch) return;
      if (request.body.operation === 'follow_up') {
        const receipt = result.receipt;
        if (String(receipt?.job_id) !== request.path.split('/').at(-2) || receipt.request_id !== request.body.request_id || receipt.text !== request.body.text || receipt.operation !== 'follow_up') throw new Error('mismatched receipt');
        if (receipt.state === 'uncertain') { notice = 'Follow-up unconfirmed. Retry uses the same request.'; return; }
        notice = 'Follow-up received. Worker application is unconfirmed.';
      } else {
        if (String(result.message?.conversation_id) !== String(id)) throw new Error('mismatched message');
        notice = 'Message saved.';
      }
      attempt = null; text = ''; drafts.delete(draftKey(activeWorker()));
    } catch (error) {
      if (!current() || mine !== epoch) return;
      if (error.status && error.status < 500) attempt = null;
      if ([401, 403, 404].includes(error.status)) { detail = {}; checkpoint = {}; project = {}; attempt = null; }
      notice = attempt ? 'Send unconfirmed. Retry uses the same request.' : 'Message was not accepted. Your draft is kept.';
    } finally { busy = false; if (current() && mine === epoch) { paint(); schedule(); } }
  }
  function click(event) {
    const button = event.target.closest?.('[data-cb-worker], [data-cb-mode], [data-cb-share]');
    if (!button || !root.contains(button)) return;
    if (button.dataset.cbWorker) void choose(button.dataset.cbWorker);
    else if (button.dataset.cbMode) chooseMode(button.dataset.cbMode);
    else if (parent) void openSharing();
  }
  function input(event) { if (event.target.id === 'cb-draft') { text = event.target.value; drafts.set(draftKey(activeWorker()), text); const send = root.querySelector('.cb-send'); if (send) send.disabled = busy || !text.trim(); } }
  function submit(event) { if (event.target.matches('[data-cb-compose]')) { event.preventDefault(); void send(); } }
  function stop() {
    stopped = true; epoch++; controller?.abort(); sharing?.stop(); drafts.clear(); timers.clearTimeout(timer); doc?.removeEventListener?.('visibilitychange', visible);
    root.removeEventListener('click', click); root.removeEventListener('input', input); root.removeEventListener('submit', submit);
    root.classList.remove('capy-coworkers'); root.innerHTML = ''; if (header) header.hidden = wasHidden;
  }
  root.addEventListener('click', click); root.addEventListener('input', input); root.addEventListener('submit', submit);
  doc?.addEventListener?.('visibilitychange', visible);
  void refresh();
  return { refresh, stop };
}
