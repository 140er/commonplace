// Nested workspace pages share the existing controllers and their server authority.
import { escape as e } from './api.js';
import { workspaceRoute } from './routes.js';
import { mount as mountMap } from './deck-compute.js';
import { createCompute } from './compute.js';
import { createCoworkers } from './coworkers.js';
import { statusMark } from './topology-map.js';
import { mount as mountCapyCoworkers } from './deck-coworkers.js';

export const TITLE = 'Compute';
const enc = encodeURIComponent;
export const COWORKERS = '#/bots/-/coworkers';
export const coworkerLink = bot => `#/bots/${enc(bot)}/coworkers`;
const workerState = state => ({ running: 'up', started: 'up', queued: 'unknown', pending: 'unknown', failed: 'down', in_doubt: 'degraded', needs_person: 'degraded' })[state] || 'unknown';
const status = (state, word) => `<span class="dw-status">${statusMark(state)}<span>${e(word)}</span></span>`;

function styles(doc) {
  for (const name of ['deck-workspace', 'compute', 'coworkers']) {
    if (!doc?.head || doc.querySelector(`link[data-workspace-style="${name}"]`)) continue;
    const link = doc.createElement('link');
    link.rel = 'stylesheet'; link.href = new URL(`./${name}.css`, import.meta.url).href;
    link.dataset.workspaceStyle = name; doc.head.append(link);
  }
}

/** A project association is evidence, not a newly granted ownership relationship. */
export function coworkerRow({ bot, board = {}, project, checkpoint, failed = false }) {
  const main = project?.main;
  const children = (checkpoint?.jobs || []).filter(job => job.thread_id || job.assignee);
  return `<article class="dw-worker"><header><h2>${e(project?.name || board.name || bot.name)} Coworker</h2>${status(!failed && main?.connected ? 'up' : 'unknown', failed ? 'Unavailable' : main?.connected ? 'Connected' : 'Not connected')}</header><dl><div><dt>Bot</dt><dd><a href="#/bots/${enc(bot.id)}">${e(bot.name)}</a></dd></div><div><dt>Owns</dt><dd>${e(board.name || bot.board)}${board.repo ? `<small>${e(board.repo)}</small>` : ''}</dd></div><div><dt>Subagents</dt><dd>${children.length ? `<ul>${children.map(job => `<li><span class="dw-agent-name">${e(job.assignee || job.thread_id)}</span>${status(workerState(job.state), (job.state || 'State not recorded').replaceAll('_', ' '))}</li>`).join('')}</ul>` : 'No child threads recorded'}</dd></div></dl><p class="dw-source">${failed ? 'The project or worker status could not be read.' : 'From this Bot’s project and its recorded work.'}</p></article>`;
}

export function mount(context) {
  let ctx = context, stopped = false, epoch = 0, request = null, child = null;
  const root = context.root, page = workspaceRoute(context.route);
  const doc = root.ownerDocument || globalThis.document;
  if (page?.screen === 'coworkers' && !page.conversations && doc?.documentElement?.dataset?.ui === 'deck') return mountCapyCoworkers(context);
  styles(doc);
  root.classList.add('deck-workspace');
  const headers = (doc?.documentElement?.dataset?.ui === 'deck' ? ['.head'] : ['.head', '#phone-bar']).map(selector => root.parentElement?.querySelector(selector)).filter(Boolean).map(node => ({ node, hidden: node.hidden }));
  for (const { node } of headers) node.hidden = true;
  const navStyle = root.parentElement?.querySelector('#phone-tabs')?.style;
  const previousNav = navStyle && ['position', 'bottom', 'left', 'right', 'zIndex'].map(key => [key, navStyle[key]]);
  if (navStyle) Object.assign(navStyle, { position: 'fixed', bottom: '0', left: '0', right: '0', zIndex: '10' });
  const region = name => root.querySelector(`[data-workspace="${name}"]`);
  const host = '<div data-workspace="content"></div>';
  if (page.screen === 'compute') {
    const mapLink = doc?.documentElement?.dataset?.ui === 'deck' ? '#/compute' : '#/compute-map';
    root.innerHTML = `<nav class="dw-tabs" aria-label="Compute views"><a href="${mapLink}"${page.management ? '' : ' aria-current="page"'}>Map</a><a href="#/compute/management"${page.management ? ' aria-current="page"' : ''}>Management</a></nav>${host}`;
    if (page.management) {
      child = createCompute({ mount: () => region('content'), api: (...args) => ctx.api(...args), current: () => ctx.me, route: () => globalThis.location?.hash || ctx.route, expectedRoute: ctx.route, title: 'Compute', doc });
      child.show();
    } else {
      root.classList.add('dw-map');
      child = mountMap({ ...ctx, root: region('content'), shell: root.parentElement });
    }
  } else {
    root.innerHTML = `<nav class="dw-crumbs" aria-label="Breadcrumb"><a href="#/bots">Bots</a><span data-workspace="parent"></span><a href="${page.bot ? coworkerLink(page.bot) : COWORKERS}"${page.conversations ? '' : ' aria-current="page"'}>Coworkers</a>${page.conversations ? '<span>Conversations</span>' : ''}</nav><header class="dw-title"><div><h1>Coworkers</h1><p>Each Coworker works under a Bot, owns its project’s code and uses subagents.</p></div>${page.conversations ? '' : `<div class="dw-controls"><nav class="dw-switch" aria-label="Coworker scope">${page.bot ? `<a href="${coworkerLink(page.bot)}" aria-current="page">This Bot</a>` : '<span aria-disabled="true">This Bot</span>'}<a href="${COWORKERS}"${page.bot ? '' : ' aria-current="page"'}>All Bots</a></nav><a class="btn" href="${COWORKERS}/conversations">Open conversation</a></div>`}</header>${host}`;
    if (page.conversations) {
      child = createCoworkers({ mount: () => region('content'), api: (...args) => ctx.api(...args), current: () => ctx.me, boards: () => ctx.boards || [], doc });
      child.show(page.conversation, page.message);
    } else {
      region('content').innerHTML = '<p role="status">Loading Coworkers…</p>';
      void refresh();
    }
  }
  async function refresh(next = {}) {
    ctx = { ...ctx, ...next };
    if (stopped) return;
    if (child) { child.refresh?.(page.screen === 'compute' && !page.management ? ctx : undefined); return; }
    const mine = ++epoch;
    request?.abort(); request = new AbortController();
    const options = { signal: request.signal, timeoutMs: 10000 };
    try {
      const roster = await ctx.api('/api/bots', options);
      if (stopped || mine !== epoch) return;
      const bots = roster.bots || [], parent = bots.find(bot => bot.id === page.bot);
      region('parent').innerHTML = parent ? `<a href="#/bots/${enc(parent.id)}">${e(parent.name)}</a>` : '';
      const projects = bots.filter(bot => bot.board && (!page.bot || bot.id === page.bot));
      const rows = await Promise.all(projects.map(async bot => {
        const board = (ctx.boards || []).find(item => item.slug === bot.board) || { slug: bot.board };
        const [project, checkpoint] = await Promise.allSettled([
          ctx.api(`/api/projects/${enc(bot.board)}`, options),
          ctx.api(`/api/orch/boards/${enc(bot.board)}/roles/main/checkpoint`, options),
        ]);
        return `<div>${page.bot ? '' : `<a class="dw-parent" href="${coworkerLink(bot.id)}">${e(bot.name)} › Coworkers</a>`}${coworkerRow({ bot, board, project: project.value, checkpoint: checkpoint.value, failed: project.status === 'rejected' || checkpoint.status === 'rejected' })}</div>`;
      }));
      if (stopped || mine !== epoch) return;
      region('content').innerHTML = rows.length ? `<div class="dw-workers">${rows.join('')}</div>` : `<p class="dw-empty">${page.bot && !parent ? 'This Bot is unavailable.' : 'No project Coworker is recorded here yet.'}</p>`;
    } catch {
      if (!stopped && mine === epoch) region('content').innerHTML = '<p role="alert">Coworkers could not be loaded. Reopen this page to try again.</p>';
    }
  }
  return { refresh, stop() {
    stopped = true; epoch++; request?.abort(); child?.stop();
    root.classList.remove('deck-workspace'); root.classList.remove('dw-map'); root.innerHTML = '';
    for (const { node, hidden } of headers) node.hidden = hidden;
    if (navStyle) for (const [key, value] of previousNav) navStyle[key] = value;
  } };
}
