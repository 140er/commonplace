// Presentation of authorized Activity events; no new reads or write authority.
import { needsCards, displayText, timeHTML } from './deck-home-today.js';

export function activityText(event, bots = [], needs = {}) {
  if (event.summary) return event.summary;
  const title = event.data?.title, name = bots.find(bot => bot.board && bot.board === event.data?.board)?.name;
  const waiting = needsCards(needs).some(card => card.number === event.data?.number);
  if (!title) return event.kind === 'memory.saved' ? 'Memory was saved' : '';
  if (event.kind === 'card.review_needed' || (event.kind === 'card.created' && waiting)) return name ? `${name} asked: ${title}` : `Waiting for your decision: ${title}`;
  const verbs = { 'card.created': 'added', 'card.updated': 'updated', 'card.moved': 'made progress on', 'card.commented': 'replied to', 'card.decided': 'recorded a decision on' };
  const verb = verbs[event.kind];
  return verb ? `${name ? `${name} ${verb}` : verb[0].toUpperCase() + verb.slice(1)} ${title}` : '';
}
export function activityEvent(event, bots, needs) {
  const bot = bots.find(bot => bot.board && bot.board === event.data?.board);
  const asked = event.kind === 'card.review_needed' || (event.kind === 'card.created' && needsCards(needs).some(card => card.number === event.data?.number));
  return { id: event.seq ?? event.id ?? event.at, at: event.at, text: activityText(event, bots, needs), title: event.data?.title, request: asked ? event.data?.number : null,
    askBot: asked && bot ? { id: bot.id || bot.board, name: bot.name } : null };
}
export function groupActivity(rows) {
  const groups = [];
  for (const row of rows) {
    const at = Date.parse(row.at);
    // Anchor the burst to its first row, so repeated arrivals cannot extend it indefinitely.
    const group = row.askBot && Number.isFinite(at) && groups.find(group => group.askBot?.id === row.askBot.id && Math.abs(Date.parse(group.at) - at) <= 5000);
    if (group) {
      if (row.id < group.firstId) group.firstId = row.id;
      if (!row.request || !group.items.some(item => item.request === row.request)) group.items.push(row);
    } else groups.push({ ...row, firstId: row.id, items: [row] });
  }
  return groups.map(group => {
    if (group.items.length === 1) return group.items[0];
    return { ...group, id: JSON.stringify([group.askBot.id, group.firstId]), text: `${group.askBot.name} asked ${group.items.length} things` };
  });
}
export function activityHTML(row, e, open = false) {
  const fresh = row.fresh && Date.now() - Date.parse(row.at) < 4000;
  const content = row.items ? `<details data-bt-group="${e(row.id)}"${open ? ' open' : ''}><summary>${e(displayText(row.text))}</summary><ul class="bt-group-items">${row.items.map(item => `<li>${e(displayText(item.title || item.text))}</li>`).join('')}</ul></details>` : `<span>${e(displayText(row.text))}</span>`;
  return `<li${fresh ? ` class="bt-new-activity" data-at="${e(row.at)}"` : ''}>${timeHTML(row.at, e)}${content}</li>`;
}
