// Shared create fields and validation; each controller retains its own storage and permissions.
// Both stores accept any nonblank UTF-8 display name; their generated IDs have separate rules.
const nameLimit = ({personal = false} = {}) => personal ? 60 : 80;
const nameRule = options => `Use 1–${nameLimit(options)} characters. Spaces and non-English letters are welcome.`;
export function botNameError(value, options) {
  const name = String(value || '').trim();
  if (!name || [...name].length > nameLimit(options)) return nameRule(options);
  return /[\uD800-\uDFFF]/u.test(name) ? 'Use valid text for the name.' : '';
}
export function botCreateFields(values, e, options = {}) {
  const value = key => e(values[key] || '');
  // Native maxlength counts UTF-16 units; validate code points so emoji names match Python's len.
  return `<div data-bot-create-fields><label>Name<input name="name" data-name-limit="${nameLimit(options)}" required autocomplete="off" data-focus value="${value('name')}" aria-describedby="bot-name-help"></label><p id="bot-name-help" class="set-note">${nameRule(options)}</p>${options.personal ? `<label>Job<input name="job" maxlength="200" autocomplete="off" placeholder="What it helps with" value="${value('job')}"></label>` : ''}<label>How to work<textarea name="instructions" maxlength="4000" rows="4" placeholder="Optional: tone, habits, things to avoid">${value('instructions')}</textarea></label></div>`;
}
export function validateBotName(form, options) {
  const input = form.elements.name, error = botNameError(input.value, options);
  input.setCustomValidity?.(error);
  input.setAttribute?.('aria-invalid', String(Boolean(error)));
  const help = form.querySelector?.('#bot-name-help');
  if (help) { help.textContent = error || nameRule(options); help.setAttribute('role', error ? 'alert' : 'note'); }
  return error;
}
