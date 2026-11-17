// Shared create fields and validation; each controller retains its own storage and permissions.
export const BOT_NAME_RULE = 'Use 1–60 characters: letters A–Z, numbers, spaces or . _ : @ -, starting with a letter or number.';
export function botNameError(value) {
  const name = String(value || '').trim();
  return !name || name.length > 60 || !/^[A-Za-z0-9][A-Za-z0-9 ._:@-]*$/.test(name) ? BOT_NAME_RULE : '';
}
export function botCreateFields(values, e, {personal = false} = {}) {
  const value = key => e(values[key] || '');
  return `<div data-bot-create-fields><label>Name<input name="name" maxlength="60" required autocomplete="off" data-focus value="${value('name')}" aria-describedby="bot-name-help"></label><p id="bot-name-help" class="set-note">${BOT_NAME_RULE}</p>${personal ? `<label>Job<input name="job" maxlength="200" autocomplete="off" placeholder="What it helps with" value="${value('job')}"></label>` : ''}<label>How to work<textarea name="instructions" maxlength="4000" rows="4" placeholder="Optional: tone, habits, things to avoid">${value('instructions')}</textarea></label></div>`;
}
export function validateBotName(form) {
  const input = form.elements.name, error = botNameError(input.value);
  input.setCustomValidity?.(error);
  input.setAttribute?.('aria-invalid', String(Boolean(error)));
  const help = form.querySelector?.('#bot-name-help');
  if (help) { help.textContent = error || BOT_NAME_RULE; help.setAttribute('role', error ? 'alert' : 'note'); }
  return error;
}
