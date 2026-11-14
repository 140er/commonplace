// A read that takes over a second says so, in the place the person is already looking.
import { escape as e } from './api.js';

export const SLOW_AFTER_MS = 1000;
export const SLOW_TEXT = 'Still loading. This can take a few seconds.';
const latest = new WeakMap();
// Draws the progress text now; if that same text is still the newest thing in `host` after `after` ms, adds the slow hint.
export function showLoading(host, text, { after = SLOW_AFTER_MS, timer = setTimeout } = {}) {
  const html = `<div class="message" role="status">${e(text)}</div>`, id = Symbol();
  host.innerHTML = html; latest.set(host, id);
  timer(() => {
    if (latest.get(host) === id && host.innerHTML === html) host.innerHTML = html.replace(/<\/div>$/, ` <span data-slow>${SLOW_TEXT}</span></div>`);
  }, after)?.unref?.();
}
