// The one definition of "needs me", shared by Today, Home, the shell badges and the Board.
// A card needs the signed-in owner exactly when the owner queue (/api/needs-me) lists it; every count
// and every "Needs you" column is derived from that list, never from a second counter.

export const BUCKETS = ['approvals', 'backlog_from_agents', 'asks', 'done_review', 'ready_to_merge', 'plans', 'stale'];
const valid = card => Number.isSafeInteger(card?.number) && card.number > 0;

/** True when the payload carries the queue's lists (an owner read), false for a bare or missing queue. */
export const hasQueue = needs => BUCKETS.some(bucket => Array.isArray(needs?.[bucket]));

/** Every queued card once, in bucket order. */
export function needsCards(needs = {}) {
  return [...new Map(BUCKETS.flatMap(bucket => Array.isArray(needs?.[bucket]) ? needs[bucket] : []).filter(valid).map(card => [card.number, card])).values()];
}

const validConfig = item => typeof item?.board === 'string' && item.board !== '' && Number.isSafeInteger(item?.version) && item.version > 0 && typeof item?.sha256 === 'string';

/** Proposed Coworker configs waiting on the owner, one per board and version. They are decisions, not cards:
 *  they have no card number, and they are counted with the cards. */
export function configProposals(needs = {}) {
  const list = Array.isArray(needs?.config_proposals) ? needs.config_proposals : [];
  return [...new Map(list.filter(validConfig).map(item => [`${item.board}:${item.version}`, item])).values()];
}

const validSetup = item => Number.isSafeInteger(item?.id) && item.id > 0 && typeof item?.sha256 === 'string' && item.sha256 !== '';

/** Proposed workspace setups waiting on the owner (bots, Coworkers and models in one approval), one per version.
 *  Like config proposals they are decisions with no card number, counted with the cards. */
export function setupProposals(needs = {}) {
  const list = Array.isArray(needs?.setup_proposals) ? needs.setup_proposals : [];
  return [...new Map(list.filter(validSetup).map(item => [item.id, item])).values()];
}

/** Machine setup steps waiting on the owner (a consent to approve or confirm). They are decisions with no card
 *  number: the server counts them with the cards, so every screen that lists the queue shows them too. */
export function runnerSetupItems(needs = {}) {
  const list = Array.isArray(needs?.runner_setup) ? needs.runner_setup : [];
  return list.filter(item => item?.kind === 'runner_consent' && typeof item.id === 'string' && item.id !== '');
}

/** How many items wait on the person. Derived from the listed cards, config proposals, setup proposals and machine
 *  setup steps; only a payload with no lists at all (a member who cannot read the queue) falls back to its plain number. */
export function needsMeCount(needs) {
  if (hasQueue(needs)) return needsCards(needs).length + configProposals(needs).length + setupProposals(needs).length + runnerSetupItems(needs).length;
  return Number.isSafeInteger(needs?.count) && needs.count > 0 ? needs.count : 0;
}

/** False while nothing has loaded yet, so a screen can show nothing instead of a false zero. */
export const needsKnown = needs => hasQueue(needs) || Number.isSafeInteger(needs?.count);

let queue = null;
/** The shell tells the screens which queue is current, so the Board groups cards by the same list as the counts. */
export const setNeedsQueue = needs => { queue = hasQueue(needs) ? needs : null; };
export const currentNeeds = () => queue;

/** True/false when a queue is loaded, null when the queue is unknown (the caller keeps the card's own flag). */
export function inNeedsQueue(card, needs = queue) {
  if (!hasQueue(needs) || !valid(card)) return null;
  return needsCards(needs).some(item => item.number === card.number);
}

// Display text only. A reference that was removed must not leave a gap behind it; text without a removed
// reference is only trimmed, so exact words such as a leading dash are never touched.
const GAP = '\u0000';
export function hideReferences(value, ...patterns) {
  const marked = patterns.reduce((text, pattern) => text.replace(pattern, GAP), String(value ?? ''));
  return (marked.includes(GAP) ? tidyGaps(marked) : marked).replace(/[ \t]{2,}/g, ' ').trim();
}
export function tidyGaps(text) {
  return String(text)
    // a connector word that now points at nothing goes with it: "see #12" -> ""
    .replace(/(?:\b(?:see|in|from|per|via|fixes|fixed|closes|closed|refs?|about|and|or|for|of|on|at|to|with)[ \t]*)+\u0000/gi, GAP)
    // "#1, #2 and #3" -> one gap
    .replace(/\u0000(?:[ \t]*(?:,|and|or|&)[ \t]*\u0000)+/gi, GAP)
    .replace(/\([ \t]*\u0000[ \t]*\)|\[[ \t]*\u0000[ \t]*\]/g, GAP)
    .replace(/^\u0000[ \t]*[,;:.\-–—]*[ \t]*/, '')
    .replace(/[,;:][ \t]*\u0000[ \t]*(?=[,;:.!?)\]]|$)/g, '')
    .replace(/[ \t]*\u0000[ \t]*(?=[,;:.!?)\]])/g, '')
    .replace(/([(\[])[ \t]*\u0000[ \t]*/g, '$1')
    .replace(/[ \t]*\u0000[ \t]*$/, '')
    .replace(/[ \t]*\u0000[ \t]*/g, ' ')
    .replace(/\b(?:and|or)[ \t]*(?=[,.;:!?)\]]|$)/gi, '');
}

/** A short plain preview: whole words, at most `max` characters, "…" when it was cut. */
export function previewText(text, max = 140) {
  const flat = String(text ?? '').replace(/\s+/g, ' ').trim();
  if (flat.length <= max) return flat;
  const cut = flat.slice(0, max);
  const space = cut.lastIndexOf(' ');
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[\s,;:.\-–—]+$/, '')}…`;
}
