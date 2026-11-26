// A page's main creating action in the desktop title bar (#head-actions); a phone keeps it in the page.
// The page renders its action as usual and seats that same element here, so its handler and data attributes
// are untouched. The element goes back to its place in the page on a phone, and when the page lets go of it.
const WIDE = "(min-width: 700px)";
let seated = null;
let watching = false;

const wide = (doc) => doc?.documentElement?.dataset?.ui === "deck" && globalThis.matchMedia?.(WIDE)?.matches === true;
const bar = (doc) => doc?.querySelector?.("#head-actions") ?? null;

function home(entry) {
  const { node, home: spot } = entry;
  const parent = spot?.parent;
  if (parent?.isConnected) parent.insertBefore(node, spot.next?.parentNode === parent ? spot.next : null);
  else node.remove?.();
}

function place() {
  if (!seated) return;
  const { node, home: spot, doc } = seated;
  // The page moved on (its title bar and content were redrawn): nothing left to place.
  if (!node.isConnected && !spot?.parent?.isConnected) { seated = null; return; }
  const host = wide(doc) ? bar(doc) : null;
  if (host) { if (node.parentNode !== host) host.append(node); } else if (node.parentNode === bar(doc)) home(seated);
}

function watch() {
  if (watching) return;
  const query = globalThis.matchMedia?.(WIDE);
  if (!query?.addEventListener) return;
  query.addEventListener("change", place);
  watching = true;
}

/** Seat a page's action element in the desktop bar; true when it is there now. */
export function seatActions(node) {
  const doc = node?.ownerDocument;
  if (!doc) return false;
  releaseActions();
  seated = { node, doc, home: node.parentNode ? { parent: node.parentNode, next: node.nextSibling } : null };
  watch();
  place();
  const host = bar(doc);
  return host !== null && node.parentNode === host;
}

/** Give the element back to its page (or drop it when the page is gone); no argument releases whatever is seated. */
export function releaseActions(node) {
  if (!seated || (node && seated.node !== node)) return;
  const entry = seated;
  seated = null;
  home(entry);
}

/** One handler for both places an action can sit: the page's own listener and the bar's see the same click once. */
export function once(handler) {
  const seen = new WeakSet();
  return (event) => { if (seen.has(event)) return undefined; seen.add(event); return handler(event); };
}

/** The element a page seated in `head`, so a title redraw can put it back; null when none is there. */
export const seatedIn = (head) => (seated && seated.node.parentNode === head ? seated.node : null);
