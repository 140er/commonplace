// A person moving a card between the Deck board's columns. The server stays the authority on who
// may move what; this only names the destinations the way the board does and says plainly why a
// move did not happen.
import { boardGroup } from './deck-board.js';

const NAMES = { todo: 'Needs you', doing: 'Active', review: 'In review', done: 'Shipped' };
// Needs you holds what waits for a person, so it is never somewhere a card is moved to.
const COLUMNS = ['doing', 'review', 'done'];
// The status a person's move to each column sets, by the card's status now.
const FROM = {
  backlog: { doing: 'todo' },
  todo: { doing: 'doing', review: 'review' },
  doing: { review: 'review', done: 'done' },
  review: { doing: 'doing', done: 'done' },
  done: { doing: 'todo' },
};
const NOTE = { todo: 'Start it', done: 'Reopen it' };
export const BUSY = 'The workspace is busy for a moment. Nothing was lost; try the move again in a few seconds.';

export const columnName = (card, me) => NAMES[boardGroup(card, me)];

export function moveOptions(card, me) {
  const here = boardGroup(card, me);
  const asked = card.status === 'review' && card.review_kind !== 'moved';
  const locked = asked ? 'Answer its question first.' : card.proposal_status === 'pending' ? 'Approve or reject the proposal first.' : '';
  return COLUMNS.map((column) => {
    const status = locked ? undefined : FROM[card.status]?.[column];
    const current = !status && here === column;
    const base = { column, name: NAMES[column], status, current };
    if (status) return { ...base, note: card.status === 'todo' && column === 'doing' ? NOTE.todo : card.status === 'done' ? NOTE.done : '' };
    return { ...base, disabled: true, note: current ? 'now here' : locked || 'Move it to Active first.' };
  });
}

// What a refused move means for the person: whether it already happened, can be sent again with
// the current version, or needs saying in words.
export function moveFailure(error, { saw, wanted }) {
  const payload = error?.payload || {};
  if (error?.status === 409 && payload.current) {
    if (payload.current.status === wanted) return { applied: payload.current };
    if (payload.current.status === saw) return { retry: payload.current.version };
    return { text: `Someone else already changed this card, so it was not moved. It was reloaded; move it again if you still want to.` };
  }
  if (error?.status === 409 && payload.code === 'busy') return { text: BUSY };
  return { text: error?.message || 'Could not move this card. Try again.' };
}

// PATCH the status. A card that already reached the status (a second tap) counts as moved, and a
// card that only changed elsewhere (a heartbeat, a new link) is sent again once with its version.
export async function putMove(api, card, status, extra = {}) {
  let version = card.version;
  for (let attempt = 0; ; attempt++) {
    try { return await api(`/api/cards/${card.number}`, { method: 'PATCH', body: { version, status, ...extra } }); }
    catch (error) {
      const failure = moveFailure(error, { saw: card.status, wanted: status });
      if (failure.applied) return failure.applied;
      if (failure.retry !== undefined && attempt === 0) { version = failure.retry; continue; }
      throw Object.assign(new Error(failure.text || 'The card changed while you were moving it. It was reloaded; try again.'), { plain: true, status: error?.status });
    }
  }
}

// The words for any refused card write that came back 409.
export function conflictText(error, serverReason = false) {
  const code = error?.payload?.code;
  if (code === 'busy') return BUSY;
  if (error?.plain || code || serverReason) return error.message;
  return 'This card changed while you were looking at it. It was reloaded; check it and try again.';
}
