// The compact sharing drawer uses the existing settings controller and routes.
export function scrollFades(root) {
  for (const node of root?.querySelectorAll?.('[data-cb-scroll]') || []) {
    const update = () => {
      const start = node.scrollLeft > 2, end = node.scrollWidth - node.clientWidth - node.scrollLeft > 2;
      node.dataset.fade = start && end ? 'both' : start ? 'start' : end ? 'end' : 'none';
    };
    node.onscroll = update; update();
  }
}
export const sharePermission = value => ({ talk: value === 'talk' || value === 'both', see_shared: value === 'read' || value === 'both' });
const permissionValue = row => row.talk && row.see_shared ? 'both' : row.talk ? 'talk' : row.see_shared ? 'read' : '';

export function compactSharingHTML(sheet, bot, e, { accessLogText, accessLogTime, permissionText }) {
  const editable = bot.mine === true && !bot.frozen && !bot.lifecycle && bot.kind === 'custom' && sheet.settings?.sharing === true;
  const off = sheet.busy || sheet.loading, selected = sheet.selected;
  const rows = [...(sheet.shares || [])];
  if (selected && !rows.some(row => row.user.id === selected)) rows.push({ user: { id: selected, name: sheet.me.people[`user:${selected}`] }, talk: false, see_shared: false });
  const options = [['talk', 'Can talk'], ['read', 'Can read shared'], ['both', 'Can talk + read']];
  const people = Object.entries(sheet.me?.people || {}).filter(([ref]) => ref.startsWith('user:') && ref !== `user:${bot.owner?.id}` && !rows.some(row => ref === `user:${row.user.id}`));
  const search = String(sheet.search || '').toLocaleLowerCase();
  const log = `<section class="bot-access-card cb-access-log" data-access-log><h3>Access log</h3>${sheet.log?.length ? `<ol>${sheet.log.map(row => `<li><p>${e(accessLogText(row, sheet.me?.people))}</p><time datetime="${e(row.at)}">${e(accessLogTime(row.at))}</time></li>`).join('')}</ol>` : '<p>No access changes yet.</p>'}${sheet.moreLog ? '<button type="button" class="bb-opt" data-bots-action="sharing-more">More changes</button>' : ''}</section>`;
  return `${sheet.loading ? '<p role="status">Loading bot settings…</p>' : ''}${editable ? `<label class="cb-share-search"><span class="sr">Add a person by name</span><input type="search" name="person-search" placeholder="Add a person by name" value="${e(sheet.search || '')}" autocomplete="off"></label><ul class="bot-access-people cb-share-results" data-capy-results${search ? '' : ' hidden'}>${people.map(([ref, person]) => `<li data-person-name="${e(person.toLocaleLowerCase())}"${search && person.toLocaleLowerCase().includes(search) ? '' : ' hidden'}><span>${e(person)}</span><button type="button" class="bb-opt" data-bots-action="sharing-select" data-user="${e(ref.slice(5))}"${off || selected ? ' disabled' : ''}>Add</button></li>`).join('')}</ul>` : ''}<section class="bot-access-card" data-people-access><h3 class="sr">People with access</h3><ul class="bot-access-people cb-share-people"><li><div><b>${e(bot.owner?.name || 'You')}</b><small>${bot.mine ? 'You' : 'Bot owner'}</small></div></li>${rows.map(row => {
    const active = row.user.id === selected, value = permissionValue(active ? sheet.draft || {} : row);
    return `<li><div><b>${e(row.user.name)}</b></div><label class="sr" for="cb-permission-${e(row.user.id)}">Access for ${e(row.user.name)}</label><select id="cb-permission-${e(row.user.id)}" data-capy-permission="${e(row.user.id)}"${!editable || off || selected && !active ? ' disabled' : ''}>${!value ? '<option value="">Choose access</option>' : ''}${options.map(([key, label]) => `<option value="${key}"${value === key ? ' selected' : ''}>${label}</option>`).join('')}</select></li>`;
  }).join('')}</ul>${!rows.length ? '<p class="cb-only-you">Only you.</p>' : ''}${sheet.saved ? '<p role="status" data-access-saved>Access saved.</p>' : ''}</section>${selected && editable ? `<section class="bot-access-preview" aria-live="polite"><h3>Before you save</h3><p>${e(permissionText(sheet.draft || {}, sheet.me.people[`user:${selected}`]))}</p><p>Private conversations stay private. Newly added people see only new shared messages.</p><button type="button" class="bb-opt" data-bots-action="sharing-cancel"${off ? ' disabled' : ''}>Cancel</button></section>` : ''}${log}${sheet.confirmStop ? `<section class="bot-access-preview" role="group" aria-label="Stop sharing"><p>Remove access for ${e((sheet.shares || []).map(row => row.user.name).join(', '))}? Their private conversations stay private.</p><button type="button" class="bb-opt" data-bots-action="sharing-stop-cancel"${off ? ' disabled' : ''}>Cancel</button></section>` : ''}`;
}

export function compactSettingsFrame(sheet, bot, content, nav, e) {
  const editable = bot.mine === true && !bot.frozen && !bot.lifecycle && bot.kind === 'custom' && sheet.settings?.sharing === true;
  const sharing = sheet.kind === 'sharing', off = sheet.busy || sheet.loading;
  const ready = editable && sheet.selected && (sheet.draft?.talk || sheet.draft?.see_shared);
  return `<div class="sheet bot-settings-sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-h"><div class="sheet-body bot-access cb-compact-sharing"><div class="cb-sharing-scroll"><header class="cb-share-top"><span class="cb-avatar" aria-hidden="true">${e(Array.from(String(bot.name || '?'))[0])}</span><span>${e(bot.name)}</span><button type="button" class="bb-opt" data-bots-action="close" data-focus aria-label="Close">×</button></header><h2 id="sheet-h">${sharing ? `Who can use ${e(bot.name)}` : "Bot settings"}</h2>${sharing ? '<p class="cb-share-explanation">Each person’s own permissions still apply.</p>' : ""}${nav}<div class="cb-share-content">${sheet.error ? `<p role="alert">${e(sheet.error)}</p>` : ''}${content}</div></div>${sharing ? `<footer class="cb-share-footer"><form data-bots-form="share"><button type="submit" class="bb-opt go"${off || !ready ? ' disabled' : ''}>Save</button></form>${editable ? `<button type="button" class="bot-danger" data-bots-action="sharing-stop"${off || !sheet.shares?.length ? ' disabled' : ''}>${sheet.confirmStop ? 'Stop sharing now' : 'Stop sharing'}</button>` : ''}</footer>` : ''}</div></div>`;
}
