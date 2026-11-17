import {escape as e} from './api.js';

const settings = board => board
  ? `<a href="#/board/${encodeURIComponent(board)}" data-action="coordinator" data-board="${e(board)}">Project settings</a>`
  : '<a href="#/boards">Project settings</a> (choose a project), or <a href="#/settings">Workspace settings</a> for a local model';

// Only server outcome codes choose a fix. Unknown failures never invent a missing prerequisite.
export function coworkerRecoveryHTML(job, board) {
  const code = job.code || job.state;
  if (['needs_configuration', 'needs_turn_on'].includes(code)) return `Needs a model: choose one in ${settings(board)}, under Chat answers. Then send your message again.`;
  if (['key_missing', 'key_not_allowed'].includes(code)) return `The chat model has no key available for this project. Choose an allowed key in ${settings(board)}, under Chat answers. Then send your message again.`;
  if (code === 'runner_not_configured') return 'Needs a machine: <a href="#/admin">Add machine</a>. The task remains available; open it before starting work.';
  if (['failed', 'unavailable', 'interrupted', 'uncertain'].includes(job.state)) {
    const reason = code === 'provider_unavailable' ? 'The provider could not be reached.' : code === 'timeout' ? 'The bot took too long to answer.' : job.state === 'uncertain' ? 'The response outcome is unconfirmed. Check the conversation before sending again.' : 'The bot could not complete this response.';
    return `${reason} <a href="#/bots/-/coworkers/conversations/${encodeURIComponent(job.conversation_id)}?message=${encodeURIComponent(job.message_id)}" data-cw-action="retry-reply">Try again in the conversation</a>.`;
  }
  return '';
}
