import { escape as e } from './api.js';

// The one segment control. The board filter and the Needs me filter both render it, so look,
// type and size change in one place. Callers own placement (margins, width) in their own sheet.
const host = ':is(.workbench-board, .needs-me)';
export const segStyle = `
${host} .seg { display: inline-flex; border: 1px solid var(--line-strong); border-radius: var(--r); overflow: hidden; background: var(--surface); }
${host} .seg button { border: 0; background: none; min-height: 30px; padding: 0 10px; color: var(--text-2); font-size: 13px; font-weight: 500; }
${host} .seg button + button { border-left: 1px solid var(--line); }
${host} .seg button[aria-pressed="true"] { background: var(--text); color: var(--surface); }
@media (max-width: 699px) {
  ${host} .seg { max-width: 100%; overflow-x: auto; }
  ${host} .seg button { min-height: 44px; }
}`;

// items: [[key, label html]]; `attr` is the data attribute that carries the key.
export function segHTML({ label, attr, current, items, className = '' }) {
  const buttons = items.map(([key, html]) => `<button ${attr}="${e(key)}" aria-pressed="${current === key}">${html}</button>`).join('');
  return `<div class="seg${className ? ` ${className}` : ''}" role="group" aria-label="${e(label)}">${buttons}</div>`;
}

// The app's CSP is style-src 'self': adopt a constructed sheet once, whichever view loads first.
if (globalThis.document?.adoptedStyleSheets) {
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(segStyle);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
}
