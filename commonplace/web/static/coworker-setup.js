import {escape as e} from './api.js';

const settings = board => board
  ? `<a href="#/board/${encodeURIComponent(board)}" data-action="coordinator" data-board="${e(board)}">Project settings</a>`
  : '<a href="#/boards">Project settings</a> (choose a project), or <a href="#/settings">Workspace settings</a> for a local model';

// The checklist and job recovery share the same next-step wording.
export const modelStep = code => ['needs_configuration', 'needs_turn_on'].includes(code)
  ? {before: 'Needs a model: choose one in ', after: ', under Chat answers. Then send your message again.'}
  : ['key_missing', 'key_not_allowed'].includes(code)
    ? {before: 'The chat model has no key available for this project. Choose an allowed key in ', after: ', under Chat answers. Then send your message again.'} : null;
export const machineStep = 'Needs a machine: ';

// Only server outcome codes choose a fix. Unknown failures never invent a missing prerequisite.
export function coworkerRecoveryHTML(job, board) {
  const code = job.code || job.state;
  const step = modelStep(code);
  if (step) return `${step.before}${settings(board)}${step.after}`;
  if (code === 'runner_not_configured') return `${machineStep}<a href="#/admin">Add machine</a>. The task remains available; open it before starting work.`;
  if (['failed', 'unavailable', 'interrupted', 'uncertain'].includes(job.state)) {
    const reason = code === 'provider_unavailable' ? 'The provider could not be reached.' : code === 'timeout' ? 'The bot took too long to answer.' : job.state === 'uncertain' ? 'The response outcome is unconfirmed. Check the conversation before sending again.' : 'The bot could not complete this response.';
    return `${reason} <a href="#/bots/-/coworkers/conversations/${encodeURIComponent(job.conversation_id)}?message=${encodeURIComponent(job.message_id)}" data-cw-action="retry-reply">Try again in the conversation</a>.`;
  }
  return '';
}
