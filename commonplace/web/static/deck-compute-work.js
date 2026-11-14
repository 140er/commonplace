import { escape as e } from './api.js';
import { STATE_WORDS } from './cardruns.js';

// What is running on the machines, read from the per-machine agents part of GET /api/topology/node (start jobs
// that are queued, leased, starting, uncertain, or started with no end reported). Nothing here is a new
// measurement: a count is a count of those rows, "stuck" is derived from their state and age, and "last
// activity" is the newest run update or worker connection use among the machines that could be read.

/** A start that has waited this long without the machine picking it up is called stuck. */
export const STUCK_AFTER_MS = 30 * 60 * 1000;
/** At most this many machines are read for the status line. */
export const MAX_MACHINES = 20;
const WAITING = new Set(['queued', 'leased', 'starting']);
const ORDER = { stuck: 0, running: 1, waiting: 2 };
const KIND_WORDS = { running: 'Running', waiting: 'Waiting', stuck: 'Stuck' };

const stamp = value => { const at = Date.parse(value); return Number.isFinite(at) ? at : null; };

/** `running`, `waiting` or `stuck` for one run row of the agents read. */
export function runKind(run, now) {
  if (run.state === 'uncertain') return 'stuck';
  if (run.state === 'started') return 'running';
  if (!WAITING.has(run.state)) return null;
  const at = stamp(run.updated_at);
  return at !== null && now - at > STUCK_AFTER_MS ? 'stuck' : 'waiting';
}

/** The `[node key, runner id]` pairs of the topology's machines that are enrolled runners. */
export function runnerNodes(payload) {
  return (Array.isArray(payload?.nodes) ? payload.nodes : [])
    .filter(node => typeof node?.key === 'string' && typeof node.values?.runner_id === 'string' && node.values.runner_id)
    .map(node => node.key).slice(0, MAX_MACHINES);
}

/**
 * `reads` is one entry per machine: `{ ok, agents }` where `agents` is the `agents` object of the node read.
 * `known` is false when no machine could be read; `partial` when some could not.
 */
export function workModel(reads, now = Date.now()) {
  const good = reads.filter(read => read?.ok && read.agents?.applicable);
  const jobs = [];
  let last = null;
  for (const { agents } of good) {
    const machine = agents.machine?.label || agents.machine?.id || 'a machine';
    for (const run of Array.isArray(agents.runs) ? agents.runs : []) {
      const kind = runKind(run, now);
      if (!kind) continue;
      jobs.push({ ...run, kind, machine, at: stamp(run.updated_at) });
    }
    for (const connection of Array.isArray(agents.connections) ? agents.connections : []) if (connection.kind === 'worker') last = Math.max(last ?? 0, stamp(connection.last_used_at) ?? 0) || last;
  }
  for (const job of jobs) if (job.at !== null) last = Math.max(last ?? 0, job.at);
  jobs.sort((a, b) => ORDER[a.kind] - ORDER[b.kind] || (b.at ?? 0) - (a.at ?? 0));
  const count = kind => jobs.filter(job => job.kind === kind).length;
  return { known: good.length > 0, partial: good.length < reads.length, jobs, running: count('running'), waiting: count('waiting'), stuck: count('stuck'), last };
}

const minutes = ms => Math.max(0, Math.floor(ms / 60000));
/** "just now", "5 minutes ago", "2 hours ago", "3 days ago". */
export function agoWords(at, now = Date.now()) {
  if (at === null || at === undefined) return '';
  const m = minutes(now - at), unit = (n, word) => `${n} ${word}${n === 1 ? '' : 's'} ago`;
  return m < 1 ? 'just now' : m < 60 ? unit(m, 'minute') : m < 1440 ? unit(Math.floor(m / 60), 'hour') : unit(Math.floor(m / 1440), 'day');
}

/** The one plain status line. */
export function statusText(model, now = Date.now()) {
  if (!model?.known) return 'Running work is not reported for these machines yet, so it cannot be counted here.';
  const { running, waiting, stuck } = model;
  const parts = [`${running} running, ${waiting} waiting, ${stuck} stuck.`];
  if (stuck) parts.push('Look at the flagged jobs below.');
  else parts.push(running || waiting ? 'Nothing is stuck.' : 'Nothing is running and nothing is stuck.');
  parts.push(model.last === null ? 'No activity recorded yet.' : `Last activity ${agoWords(model.last, now)}.`);
  if (model.partial) parts.push('Some machines could not be read.');
  return parts.join(' ');
}

const cardHref = job => `#/card/${encodeURIComponent(job.card)}`;
function stuckHTML(job, now) {
  const link = `<a href="${e(cardHref(job))}">open the card</a>`;
  const waited = job.at !== null ? ` It has waited ${minutes(now - job.at)} minutes.` : '';
  return job.state === 'uncertain'
    ? `<p class="dc-stuck"><strong>Stuck.</strong> It is not clear whether it started. Next step: check the machine, then ${link} and mark it as started or failed.</p>`
    : `<p class="dc-stuck"><strong>Stuck.</strong> The machine has not picked it up.${e(waited)} Next step: check that the machine is on, or ${link} and cancel the start.</p>`;
}

/** The running jobs, each with its card, bot and machine; stuck ones first, flagged with a next step. */
export function jobsHTML(model, bots = [], now = Date.now()) {
  if (!model?.known) return '';
  if (!model.jobs.length) return '<li class="dc-job-empty">No jobs are running.</li>';
  const botName = board => bots.find(bot => bot?.board === board)?.name;
  return model.jobs.map(job => {
    const state = job.state === 'started' ? 'Running' : STATE_WORDS[job.state] || job.state;
    const who = botName(job.board) || job.project || job.board;
    return `<li class="dc-job" data-job="${e(job.job)}" data-kind="${job.kind}"><a href="${e(cardHref(job))}">Card #${e(job.card)}</a> · <span class="dc-job-bot">${e(who)}</span> · on ${e(job.machine)} · <span class="dc-job-state">${e(job.kind === 'stuck' ? KIND_WORDS.stuck : state)}</span>${job.at !== null ? ` · updated ${e(agoWords(job.at, now))}` : ''}${job.kind === 'stuck' ? stuckHTML(job, now) : ''}</li>`;
  }).join('');
}
