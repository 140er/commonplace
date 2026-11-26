// A person moving a card between the Deck board's columns. The server stays the authority on who
// may move what; this only names the destinations the way the board does and says plainly why a
// move did not happen.
import { boardGroup, STATUS_NAMES } from './deck-board.js';

const NAMES = { todo: 'Waiting on you', doing: 'Active', review: 'In review', done: 'Shipped' };
// The lane each status shows in on the board (a card that waits on a person shows in Waiting on you instead).
const LANE = { backlog: 'Off the board lanes', todo: NAMES.doing, doing: NAMES.doing, review: NAMES.review, done: NAMES.done };
// The statuses a person may move a card to, by the status it has now. The server stays the authority and refuses the rest.
const FROM = {
  backlog: ['backlog', 'todo'],
  todo: ['backlog', 'todo', 'doing', 'review'],
  doing: ['backlog', 'todo', 'doing', 'review', 'done'],
  review: ['backlog', 'todo', 'doing', 'review', 'done'],
  done: ['backlog', 'todo', 'done'],
};
// What to do first when a status cannot be reached directly.
const FIRST = { backlog: 'Todo', done: 'Todo' };
// A person's move to Doing only marks the card started: no run begins from it, except that typed work with
// an assigned holder starts for that holder. Say which one, so the entry never promises a run it does not make.
const startNote = (card) => (card.task_type && card.assignee ? 'Starts its assigned work' : 'Marks it as started');
// The column a status asked for ends up in when the move works (a reopened card goes back to Active).
const WANTED = { todo: 'doing', doing: 'doing', review: 'review', done: 'done' };
export const BUSY = 'The workspace is busy for a moment. Nothing was lost; try the move again in a few seconds.';

// The column and words come from the card the server answered with (null: not the queue read before the move).
export const columnName = (card, me) => NAMES[boardGroup(card, me, null)];

// What to tell the person after a move, from the card's real state, and plainly when it is not where they asked.
export function moveNotice(card, me, asked) {
  const got = boardGroup(card, me, null), wanted = WANTED[asked];
  const status = STATUS_NAMES[card.status] || STATUS_NAMES[asked];
  if (wanted && wanted !== got) return `Moved to ${status}. It now shows in ${NAMES[got]}, not ${NAMES[wanted]}.`;
  return card.status === 'backlog' ? `Moved to ${status}.` : `Moved to ${status}. It shows in ${NAMES[got]}.`;
}

// Every status, in board order, with the lane it shows in; the ones the person may not move to are listed with why.
export function moveOptions(card, me) {
  const asked = card.status === 'review' && card.review_kind !== 'moved';
  const locked = asked ? 'Answer its question first.' : card.proposal_status === 'pending' ? 'Approve or reject the proposal first.' : '';
  return Object.keys(STATUS_NAMES).map((status) => {
    const base = { status, name: STATUS_NAMES[status], current: status === card.status };
    if (base.current) return { ...base, disabled: true, note: locked || 'now here' };
    if (locked) return { ...base, disabled: true, note: locked };
    if (!FROM[card.status]?.includes(status)) {
      return { ...base, disabled: true, note: `Move it to ${FIRST[card.status] || 'Doing'} first.` };
    }
    const lane = LANE[status];
    const how = status === 'doing' && card.status === 'todo' ? ` · ${startNote(card)}` : card.status === 'done' && status !== 'backlog' ? ' · Reopens it' : '';
    return { ...base, note: `${lane}${how}` };
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
