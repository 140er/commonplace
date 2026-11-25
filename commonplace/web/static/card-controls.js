import { escape as e } from './api.js';

export const personTimezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
const wallParts = (date, timezone) => Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
  timeZone: timezone, year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23',
}).formatToParts(date).map(part => [part.type, part.value]));
const dayText = p => `${p.year}-${p.month}-${p.day}`;

// Calendar arithmetic only. The existing snooze service resolves these wall times, including DST.
export function snoozeChoice(choice, now = new Date(), timezone = personTimezone()) {
  const p = wallParts(now, timezone), today = dayText(p);
  if (choice === 'later') {
    if (p.hour === '23' && p.minute === '59') throw new Error('There is no time left today. Choose tomorrow or pick a date/time.');
    const later = new Date(now.getTime() + 3 * 3600000);
    return {until:dayText(wallParts(later, timezone)) === today ? later.toISOString() : `${today}T23:59`, timezone};
  }
  const calendar = new Date(`${today}T09:00:00Z`);
  if (!['tomorrow', 'monday'].includes(choice)) throw new Error('Choose a return date and time.');
  calendar.setUTCDate(calendar.getUTCDate() + (choice === 'tomorrow' ? 1 : (8 - calendar.getUTCDay()) % 7 || 7));
  return {until:calendar.toISOString().slice(0,16), timezone};
}

export function snoozedText(card, timezone = personTimezone()) {
  if (!card.snoozed) return '';
  if (!card.snoozed.until) return card.snoozed.text;
  const p = wallParts(new Date(card.snoozed.until), timezone);
  return `Snoozed until ${dayText(p)} ${p.hour}:${p.minute} ${timezone}`;
}
export const snoozedLine = card => card.snoozed ? `<p class="card-snoozed" data-card-snoozed>${e(snoozedText(card))}</p>` : '';
const currentGoal = card => card.goals?.chain?.at(-1);

export function cardControls(card, {owner = false} = {}) {
  const goal = currentGoal(card), timezone = personTimezone();
  const schedule = !owner ? '' : card.snoozed ? '<button type="button" class="btn" data-card-wake>Wake now</button>' : ['backlog','todo'].includes(card.status) ? `<details data-card-snooze><summary>Snooze</summary><p>Times in ${e(timezone)}. Later today is in 3 hours, or 23:59.</p><div class="snooze-choices">${[['later','Later today'],['tomorrow','Tomorrow 09:00'],['monday','Next Monday 09:00']].map(([key,label]) => `<button type="button" class="btn" data-card-snooze-choice="${key}">${label}</button>`).join('')}</div><label>Pick a date/time<input type="datetime-local" data-card-snooze-at></label><button type="button" class="btn" data-card-snooze-save>Snooze until then</button></details>` : '';
  return `<section class="card-scheduling" data-card-controls data-goal-id="${e(goal?.id || '')}" aria-label="Schedule and goal">${snoozedLine(card)}${schedule}<div class="card-goal"><label for="card-goal">Goal</label>${owner ? `<select id="card-goal" data-card-goal disabled><option value="${e(goal?.id || '')}">${e(goal?.title || 'No goal')}</option></select><button type="button" class="btn" data-card-goal-save disabled>Save goal</button>` : `<p>${e(goal?.title || 'No goal')}</p>`}</div><p role="alert" data-card-controls-error></p></section>`;
}

// Reads never delay the card panel. A failed list keeps the current goal visible and refuses editing.
export async function loadCardGoals(mount, card, api) {
  const select = mount.querySelector('[data-card-goal]'), save = mount.querySelector('[data-card-goal-save]');
  if (!select) return;
  try {
    const {goals = []} = await api('/api/goals', {timeoutMs:15000});
    const held = currentGoal(card), listed = goals.filter(goal => !goal.archived || goal.id === held?.id);
    if (held && !listed.some(goal => goal.id === held.id)) listed.push(held);
    select.innerHTML = '<option value="">No goal</option>' + listed.map(goal => `<option value="${e(goal.id)}">${e(goal.title)}${goal.archived ? ' (archived)' : ''}</option>`).join('');
    select.value = String(held?.id || '');
    select.disabled = false;
    save.disabled = false;
  } catch (error) {
    mount.querySelector('[data-card-controls-error]').textContent = error.message;
  }
}

export async function saveCardControl(button, mount, card, api) {
  if (button.hasAttribute('data-card-wake')) return api(`/api/card-snooze/${card.number}/cancel`, {method:'POST', body:{}});
  if (button.hasAttribute('data-card-goal-save')) {
    const value = mount.querySelector('[data-card-goal]').value;
    await api('/api/goals/card', {method:'POST', body:{task:card.number, goal:value ? Number(value) : null}});
    return api(`/api/cards/${card.number}`);
  }
  const body = button.hasAttribute('data-card-snooze-choice') ? snoozeChoice(button.dataset.cardSnoozeChoice) : {until:mount.querySelector('[data-card-snooze-at]').value, timezone:personTimezone()};
  if (!body.until) throw new Error('Choose a return date and time.');
  return api(`/api/card-snooze/${card.number}`, {method:'POST', body});
}
