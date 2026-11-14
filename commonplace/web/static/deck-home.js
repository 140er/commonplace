// The hero uses the same bot SSE sender and coworker message route as chat.
import { escape as e } from './api.js';
import { sendMessage } from './botstream.js';
import { composerHTML, sendButtonHTML } from './bots.js';
import { addStyles, runHTML, displayText, quietSelector, runUpdate, retryIntent } from './deck-home-today.js';

export const TITLE = 'Commonplace';
const enc = encodeURIComponent;
const pending = run => ['sending', 'streaming', 'running', 'polling', 'dropped', 'pending', 'queued'].includes(run.state);
const effortLabels = { low: 'Low', medium: 'Medium', high: 'High', xhigh: 'Extra high', max: 'Max' };
export function modelControls(conversation, catalog = [], configured = null) {
  // The additive fields are the capability marker from the coworker turn API.
  if (!conversation || !Object.hasOwn(conversation, 'reasoning_effort') || !Object.hasOwn(conversation, 'model')) return { models: [], efforts: [] };
  const provider = configured?.ready && configured.label?.split('/')[0];
  const models = [...new Set([conversation.model, ...catalog.filter(item => provider && item.provider === provider).map(item => item.model)].filter(value => typeof value === 'string' && value))];
  return { models, efforts: Object.keys(effortLabels) };
}
export function mount(context) {
  const { root, api } = context, doc = root.ownerDocument || globalThis.document;
  addStyles(doc);
  const host = root.closest?.('.center') || root;
  host.classList.add('deck-hero');
  root.innerHTML = `<div class="bh-wrap"><div class="bh-content"><section class="bh-runs" aria-label="Your runs" aria-live="polite"></section><form class="bh-form"><div class="bh-context">${quietSelector('bot', 'Bot', 'bh-bot')}${quietSelector('coworker', 'Coworker', 'bh-coworker')}${quietSelector('machine', 'Machine', 'bh-host')}</div><div class="bh-composer">${composerHTML({ name: TITLE }, e)}<div class="bh-toolbar"><button type="button" data-bh-commands aria-label="Commands">＋</button><button type="button" data-bh-commands aria-label="Tools"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M2 4h12M2 12h12M5 2v4M11 10v4"/></svg></button>${quietSelector('model', 'Model', 'bh-model-choice')}${quietSelector('effort', 'Effort', 'bh-effort-choice')}<span class="bh-spacer"></span>${sendButtonHTML({ busy: false })}</div></div><div class="bh-mentions" role="listbox" aria-label="Mention a bot" hidden></div></form><p class="bh-hint" aria-live="polite"></p><p class="bh-error" role="alert" hidden></p><p class="bh-empty" hidden>No bots yet. <a href="#/bots?new=1">New bot</a></p></div></div>`;
  const form = root.querySelector('form'), fields = form.elements, runRegion = root.querySelector('.bh-runs'), hint = root.querySelector('.bh-hint'), errorBox = root.querySelector('.bh-error'), mentions = root.querySelector('.bh-mentions');
  let ctx = context, active = true, generation = 0, read, sending = false, configured = null, bots = [], directory = {}, catalog = [], rosterSignature = '', coworkerSignature = '', controlsSignature = '', timer = null, attempt = null;
  fields.text.placeholder = 'Ask anything, / for commands, @ for a bot';
  fields.text.setAttribute('aria-label', 'Ask anything'); fields.text.required = true; fields.text.rows = 2;
  const submit = form.querySelector('[type="submit"]'); submit.className = 'bh-send'; submit.innerHTML = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 15V5m-4 4 4-4 4 4"/></svg>'; submit.setAttribute('aria-label', 'Send');
  const runs = [], requests = new Set();
  const selectedBot = () => bots.find(bot => bot.id === fields.bot.value);
  const conversations = () => (directory.conversations || []).filter(item => item.board && item.board === selectedBot()?.board && item.bot_ids?.length === 1);
  const selectedCoworker = () => conversations().find(item => String(item.id) === fields.coworker.value);
  function report(text = '') { errorBox.textContent = text; errorBox.hidden = !text; }
  function paintRuns() { if (active) runRegion.innerHTML = runs.slice(-3).map(run => runHTML(run, e)).join(''); }
  function options(select, rows, previous = select.value) {
    select.innerHTML = rows.map(([value, label]) => `<option value="${e(value)}">${e(displayText(label))}</option>`).join('');
    if (rows.some(([value]) => String(value) === previous)) select.value = previous;
  }
  function paint() {
    if (!active) return;
    const n = ctx.needs?.count;
    hint.innerHTML = Number.isSafeInteger(n) ? n > 0 ? `<a href="#/today"><i class="bt-dot" data-tone="waiting" aria-hidden="true"></i>${n} waiting in Today</a>` : 'Nothing is waiting on you' : '';
    const signature = JSON.stringify(bots.map(bot => [bot.id, bot.name]));
    if (signature !== rosterSignature) {
      const previous = fields.bot.value;
      options(fields.bot, bots.map(bot => [bot.id, bot.name]), previous || (bots.find(bot => bot.kind === 'project') || bots[0])?.id);
      rosterSignature = signature;
    }
    root.querySelector('.bh-bot').hidden = !bots.length;
    root.querySelector('.bh-empty').hidden = !!bots.length;
    const coworkers = conversations(), coworkerKey = JSON.stringify([fields.bot.value, coworkers]);
    if (coworkerSignature !== coworkerKey) {
      options(fields.coworker, [['', 'Any coworker'], ...coworkers.map(item => [String(item.id), item.title || (directory.bots || []).find(bot => bot.id === item.bot_ids[0])?.name || 'Coworker'])]);
      coworkerSignature = coworkerKey;
    }
    root.querySelector('.bh-coworker').hidden = !coworkers.length;
    const bot = selectedBot(), coworker = selectedCoworker(), hostName = coworker?.host?.name || bot?.host?.name || (typeof bot?.host === 'string' ? bot.host : '');
    const hostLabel = root.querySelector('.bh-host'); hostLabel.hidden = !hostName; options(fields.machine, hostName ? [[hostName, hostName]] : []);
    const control = modelControls(coworker, catalog, configured), currentModel = configured?.ready && configured?.label?.split('/').slice(1).join('/'), controlKey = JSON.stringify([coworker?.id, control, coworker?.model, coworker?.reasoning_effort, currentModel]);
    if (controlKey !== controlsSignature) {
      options(fields.model, control.models.length ? [['', coworker?.model || 'Default model'], ...control.models.filter(model => model !== coworker?.model).map(model => [model, model])] : currentModel ? [['', currentModel]] : [], '');
      options(fields.effort, [['', coworker?.reasoning_effort ? effortLabels[coworker.reasoning_effort] : 'Default effort'], ...control.efforts.filter(effort => effort !== coworker?.reasoning_effort).map(effort => [effort, effortLabels[effort]])], '');
      controlsSignature = controlKey;
    }
    root.querySelector('.bh-model-choice').hidden = !control.models.length && !currentModel;
    root.querySelector('.bh-effort-choice').hidden = !control.efforts.length;
    for (const select of [fields.bot, fields.coworker, fields.machine, fields.model, fields.effort]) select.closest('label').querySelector('.bh-choice-text').textContent = select.selectedOptions[0]?.textContent || '';
    fields.text.disabled = !bot || sending;
    form.querySelector('[type="submit"]').disabled = !bot || sending || !fields.text.value.trim() || runs.some(run => pending(run) && run.bot?.id === bot.id);
  }
  async function request(path, options = {}) {
    const controller = new AbortController(); requests.add(controller);
    try { return await api(path, { ...options, signal: controller.signal, timeoutMs: 10000 }); }
    finally { requests.delete(controller); }
  }
  async function refreshRuns() {
    for (const run of runs.filter(pending)) {
      try {
        if (run.conversation) {
          const page = await request(`/api/coworkers/conversations/${enc(run.conversation)}/messages`);
          if (!active) return;
          const job = page.jobs?.find(job => String(job.id) === String(run.job));
          if (job) { runUpdate(run, job); run.steps = job.steps || (job.task_progress?.availability === 'available' ? job.task_progress.steps : []) || []; }
        } else if (run.state !== 'sending' && run.state !== 'streaming') {
          const page = await request(`/api/bots/${enc(run.bot.id)}/messages?limit=50`);
          if (!active) return;
          const message = page.messages?.find(message => message.request_id === run.requestId && message.role === 'user');
          const reply = message && page.messages?.find(item => item.role === 'bot' && item.reply_to === message.id);
          if (reply) { runUpdate(run, reply); run.steps = (reply.blocks || []).filter(block => block.type === 'action'); }
        }
      } catch (error) { if (active && [403, 404].includes(error.status)) { run.state = 'unavailable'; run.steps = []; } }
    }
    paintRuns(); paint();
  }
  function schedule() {
    clearTimeout(timer);
    if (active && runs.some(pending)) timer = setTimeout(async () => { if (!doc?.hidden) await refreshRuns(); schedule(); }, 3000);
  }
  async function refresh(next = {}) {
    if (!active) return;
    ctx = { ...ctx, ...next }; paint();
    read?.abort(); read = new AbortController(); const token = ++generation;
    try {
      const [roster, coworkers] = await Promise.allSettled([api('/api/bots', { signal: read.signal, timeoutMs: 10000 }), api('/api/coworkers', { signal: read.signal, timeoutMs: 10000 })]);
      if (!active || token !== generation) return;
      if (roster.status === 'rejected') throw roster.reason;
      bots = (roster.value.bots || []).filter(bot => !bot.frozen && !bot.lifecycle && (bot.mine !== false || bot.perms?.talk)); configured = roster.value.model;
      directory = coworkers.status === 'fulfilled' ? coworkers.value : {};
      report(); paint();
      const board = selectedBot()?.board;
      catalog = [];
      if (board && selectedCoworker()) {
        try { const project = await request(`/api/projects/${enc(board)}`); if (active && token === generation) catalog = project.model_catalog || []; } catch { /* The current model remains available. */ }
      }
      if (active && token === generation) { paint(); await refreshRuns(); }
    } catch (error) {
      if (!active || token !== generation) return;
      if ([401, 403, 404].includes(error.status)) { bots = []; directory = {}; paint(); }
      report('Could not refresh your bots. Try again.');
    }
  }
  async function onSubmit(event) {
    event.preventDefault();
    const text = fields.text.value.trim(), bot = selectedBot(), coworker = selectedCoworker();
    if (!text || text.length > 8000 || !bot || sending || runs.some(run => pending(run) && run.bot?.id === bot.id)) return;
    const body = { text, ...(coworker ? { bot_id: coworker.bot_ids[0], ...(fields.model.value ? { model: fields.model.value } : {}), ...(fields.effort.value ? { reasoning_effort: fields.effort.value } : {}) } : {}) };
    const signature = JSON.stringify([bot.id, coworker?.id, body]);
    if (attempt?.signature !== signature) attempt = { signature, requestId: globalThis.crypto.randomUUID() };
    await sendRun({ bot: { id: bot.id, name: bot.name }, conversation: coworker?.id, body, requestId: attempt.requestId });
  }
  async function sendRun(intent, previous) {
    if (sending || runs.some(run => pending(run) && run.bot?.id === intent.bot.id)) return;
    const keepDraft = !!previous; previous ||= runs.find(run => run.requestId === intent.requestId);
    const run = { bot: intent.bot, text: intent.body.text, intent, requestId: intent.requestId, conversation: intent.conversation, at: new Date().toISOString(), state: 'sending', steps: [], canConfigure: ctx.me?.can_admin === true };
    if (previous) runs.splice(runs.indexOf(previous), 1, run);
    else { runs.push(run); if (runs.length > 3) runs.shift(); }
    sending = true; report(); paintRuns(); paint();
    const controller = new AbortController(); requests.add(controller);
    try {
      if (intent.conversation) {
        const result = await api(`/api/coworkers/conversations/${enc(intent.conversation)}/messages`, { method: 'POST', body: { ...intent.body, request_id: run.requestId }, signal: controller.signal, timeoutMs: 10000 });
        if (!active) return;
        if (String(result.message?.conversation_id) !== String(intent.conversation)) throw new Error('The response did not match this conversation.');
        run.acked = true; run.job = result.job?.id;
        runUpdate(run, result.job, { state: 'done' }); run.steps = result.job?.steps || [];
      } else {
        const result = await (ctx.send || sendMessage)({ botId: intent.bot.id, text: intent.body.text, requestId: run.requestId, signal: controller.signal, onEvent(name, data) {
          if (!active) return;
          if (name === 'ack') { run.acked = true; run.state = 'streaming'; }
          if (name === 'action') run.steps[Number.isSafeInteger(data.step) ? data.step : run.steps.length] = { label: data.label, state: data.state };
          if (['done', 'error', 'running'].includes(name)) { run.terminal = true; runUpdate(run, data.message, { ...data, state: name }); }
          paintRuns();
        } });
        if (!active) return;
        run.acked ||= result.acked;
        if (['running', 'dropped'].includes(result.state)) run.state = 'polling';
        else if (!run.terminal) run.state = result.state;
      }
      if (!keepDraft) fields.text.value = '';
      attempt = null;
    } catch (error) {
      if (!active) return;
      runUpdate(run, {}, { state: 'error', text: error.message, code: error.code });
      report(error.message || 'Could not send. Your message is kept.');
    } finally { requests.delete(controller); sending = false; if (active) { paintRuns(); paint(); schedule(); } }
  }
  function openCommands() { ctx.openPalette?.(); }
  function onInput() {
    paint();
    if (fields.text.value === '/') { fields.text.value = ''; openCommands(); return; }
    const term = /(?:^|\s)@([^@\n]*)$/.exec(fields.text.value)?.[1];
    const matches = term !== undefined ? bots.filter(bot => bot.name?.toLowerCase().startsWith(term.toLowerCase())) : [];
    mentions.innerHTML = matches.map(bot => `<button type="button" role="option" data-bh-mention="${e(bot.id)}">${e(bot.name)}</button>`).join('');
    mentions.hidden = !matches.length;
  }
  function onClick(event) {
    if (event.target.closest?.('[data-bh-commands]')) openCommands();
    const retry = event.target.closest?.('[data-bh-retry]');
    if (retry) { const run = runs.find(run => run.requestId === retry.dataset.bhRetry); if (run?.intent) void sendRun(retryIntent(run), run); }
    const mention = event.target.closest?.('[data-bh-mention]');
    if (mention) {
      const bot = bots.find(bot => bot.id === mention.dataset.bhMention); if (!bot) return;
      fields.bot.value = bot.id; fields.text.value = fields.text.value.replace(/@[^@\n]*$/, `@${bot.name} `); mentions.hidden = true; paint(); fields.text.focus();
    }
  }
  function onKey(event) {
    if (event.key === 'Escape') mentions.hidden = true;
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.target === fields.text && mentions.hidden) { event.preventDefault(); form.requestSubmit(); }
    if (event.key === 'ArrowDown' && !mentions.hidden) { event.preventDefault(); mentions.querySelector('button')?.focus(); }
  }
  function onChange(event) { if (event.target === fields.bot) { fields.coworker.value = ''; catalog = []; } paint(); if ([fields.bot, fields.coworker].includes(event.target)) void refresh(); }
  form.addEventListener('submit', onSubmit); fields.text.addEventListener('input', onInput); root.addEventListener('click', onClick); root.addEventListener('keydown', onKey); form.addEventListener('change', onChange);
  paint(); void refresh();
  return { refresh, stop() {
    active = false; generation++; read?.abort(); requests.forEach(controller => controller.abort()); clearTimeout(timer);
    form.removeEventListener('submit', onSubmit); fields.text.removeEventListener('input', onInput); root.removeEventListener('click', onClick); root.removeEventListener('keydown', onKey); form.removeEventListener('change', onChange); host.classList.remove('deck-hero');
  } };
}
