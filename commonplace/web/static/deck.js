// Deck: the default look and navigation ("decisions first"). One switch per browser, kept in
// localStorage: no key (a first visit) or 'deck' is Deck, only an explicit 'classic' opts out.
// While classic is chosen nothing in this file changes what the app draws.
// On: the shell draws a top bar instead of the rail (Today, Bots, Compute, Settings), the phone gets the same
// four tabs, and deck.css re-tokens every screen (true black, lightness for elevation, amber
// only for what waits on you). Screens that exist only in Deck register in SCREENS.
import { escape as e } from './api.js';
import { workspaceRoute } from './routes.js';
import { arrangeCapySettings, capyPolicyPresentation } from './deck-settings-capy.js';
import { createDeckAppearance } from './deck-appearance.js';
import { SECTIONS, placeOf, renderShellBar, renderShellTop, renderShellTabs, renderShellMore, createDeckShell } from './deck-shell.js';
import { arrangeSettings, selectSettings } from './deck-settings.js';
import { needsMeCount } from './needs-me.js';
// Same rule as the rail's role line: an explicit role wins, otherwise a workspace admin is its owner.
const isOwner = me => me?.role ? me.role === 'owner' : me?.can_admin === true;

export const UI_KEY = 'cmmn.ui';
// Deck-only screens by route name: name -> loader of a module exporting TITLE and mount(context).
// mount returns { refresh?(context), stop?() }. A name without a loader is not offered in the bar.
export const SCREENS = {
  home: () => import('./deck-today-capy.js'),
  today: () => import('./deck-today-capy.js'),
  work: () => import('./deck-work.js'),
  compute: () => import('./deck-workspace.js'),
  pipeline: () => import('./deck-pipeline.js'),
  coworkers: () => import('./deck-workspace.js'),
};

const store = () => { try { return globalThis.localStorage || null; } catch { return null; } };
// Deck unless the browser stored exactly 'classic'; unreadable storage also means Deck.
// deck-prepaint.js repeats this rule before first paint, keep the two in step.
export function deckOn(storage = store()) {
  try { return storage?.getItem(UI_KEY) !== 'classic'; } catch { return true; }
}
export function setDeck(on, storage = store()) {
  try { storage?.setItem(UI_KEY, on ? 'deck' : 'classic'); } catch { /* The look stays as it was for this visit. */ }
}
/** Read ?ui=deck or ?ui=classic once (a link can switch the look), then mark the document. */
export function applyDeck({ doc = globalThis.document, loc = globalThis.location, hist = globalThis.history, storage = store() } = {}) {
  if (loc?.search) {
    const params = new URLSearchParams(loc.search), asked = params.get('ui');
    if (asked === 'deck' || asked === 'classic') {
      setDeck(asked === 'deck', storage);
      params.delete('ui');
      const search = params.toString();
      hist?.replaceState?.(hist.state, '', `${loc.pathname}${search ? `?${search}` : ''}${loc.hash}`);
    }
  }
  const on = deckOn(storage);
  if (doc?.documentElement) { if (on) doc.documentElement.dataset.ui = 'deck'; else delete doc.documentElement.dataset.ui; }
  return on;
}

/** The bar section a route belongs to; '' for the workspace tools reached from the account menu. */
export const deckSection = placeOf;
export const deckSections = (screens = SCREENS) => SECTIONS.filter(([id]) => id === 'bots' || id === 'settings' || screens[id]);
/** Home is the undimmed hero; older screen registries keep their fallback. */
export const deckHome = (screens = SCREENS) => screens.home ? '#/' : screens.today ? '#/today' : '#/bots';
const here = () => { try { return globalThis.location?.hash || ''; } catch { return ''; } };

// Keep the public render contract; the rail and phone chrome live in the slice module.
const shellOptions = (options = {}) => ({ ...options, hash: options.hash ?? here(), sections: deckSections(options.screens || SCREENS), section: deckSection(options.hash ?? here()), homeOn: !!(options.screens || SCREENS).home });
export const renderDeckBar = options => renderShellBar(shellOptions(options));
export const renderDeckTop = options => renderShellTop(shellOptions(options));
export const renderDeckTabs = options => renderShellTabs(shellOptions(options));
export const renderDeckMore = options => renderShellMore(shellOptions(options));

/** The Look section at the top of Settings: Deck or Classic, saved in this browser only. */
export function lookHTML(on) {
  const pick = (look, label, chosen) => `<button type="button" class="opt" data-deck-action="look" data-look="${look}" aria-pressed="${chosen ? 'true' : 'false'}">${label}</button>`;
  return `<section class="settings-section" id="look"><h2>Look</h2><p>Choose the layout of this app. Classic is the older one. The choice is saved in this browser.</p><div class="look-switch" role="group" aria-label="Look">${pick('deck', 'Commonplace', on)}${pick('classic', 'Classic', !on)}</div></section>`;
}

/** The workspace tools as one Settings section. The phone layout shows it, because a phone has no account menu. */
export function toolsHTML({ can_admin = false } = {}) {
  const row = (href, label) => `<a href="${href}">${label}</a>`;
  return `<section class="settings-section deck-tools" id="deck-tools"><h2>Workspace</h2>${row('#/needs', 'Waiting on you')}${row('#/boards', 'Projects')}${row('#/memory', 'Memory')}${row('#/console', 'Console')}${row('#/rules', 'Rules')}${can_admin === true ? row('#/admin', 'Admin') : ''}<button class="btn" data-action="switch-workspace">Switch workspace</button></section>`;
}

/** The shell's handle on Deck. Everything it needs comes in, so it runs without a browser.
 *  context() returns what a screen may read: { me, boards, needs, go(hash) }. */
export function createDeck({ root, api, context, title, screen = () => 0, reload = () => {}, screens = SCREENS, storage = store(), doc = globalThis.document } = {}) {
  const shell = createDeckShell({ root, api, context, on: () => deckOn(storage) });
  const appearance = createDeckAppearance({ root, context, on: () => deckOn(storage), doc, storage });
  let active = null;
  const name = (base) => ['#/', '#/home'].includes(base) ? 'home' : workspaceRoute(base)?.screen || /^#\/([a-z]+)(?:\?[^#]*)?$/.exec(base || '')?.[1] || '';
  const owns = (base) => typeof screens[name(base)] === 'function' && (deckOn(storage) || (!!workspaceRoute(base) && base !== '#/compute'));
  function leave() { active?.ui?.stop?.(); active = null; }
  async function show(base, { background = false, epoch = 0 } = {}) {
    const id = name(base);
    if (active?.base === base) { active.ui?.refresh?.({ ...context(), route: base }); return; }
    if (background) return;
    const module = await screens[id]();
    if (epoch !== screen()) return;
    leave();
    active = { base, ui: module.mount({ root: root.querySelector('#content'), api, e, ...context(), route: base }) };
    title(id === 'coworkers' ? 'Bot conversations' : module.TITLE || 'Commonplace');
  }
  function settings(content) {
    const box = content?.querySelector?.('.settings');
    if (!box) return;
    if (deckOn(storage) && !box.querySelector('#deck-tools')) {
      const { me, needs } = context() || {};
      box.insertAdjacentHTML('afterbegin', toolsHTML({ needs: needsMeCount(needs), can_admin: me?.can_admin }));
    }
    if (deckOn(storage)) arrangeSettings(box, { owner: isOwner(context()?.me) });
    if (!box.querySelector('#look')) box.insertAdjacentHTML('afterbegin', lookHTML(deckOn(storage)));
    appearance.settings(box);
    if (deckOn(storage)) arrangeCapySettings(box, { api, context });
  }
  // Switching restarts nothing but the current route, so the person stays on the same screen. The stage
  // and preview canvases go first: once data-ui is gone their Deck-only sizing is gone too, and a canvas
  // with no CSS size feeds its own bitmap back into its layout (the resize loop that froze the page).
  function choose(look) {
    const on = look === 'deck';
    if (on === deckOn(storage)) return;
    appearance.stop();
    setDeck(on, storage);
    applyDeck({ doc, loc: null, storage });
    leave();
    reload();
  }
  const toggle = () => choose(deckOn(storage) ? 'classic' : 'deck');
  doc?.addEventListener?.('click', (event) => {
    const look = event.target?.closest?.('[data-deck-action="look"]');
    if (look) choose(look.dataset.look);
    const jump = event.target?.closest?.('[data-deck-setting]');
    if (jump && deckOn(storage)) {
      const target = root.querySelector(`#${jump.dataset.deckSetting}`);
      const advanced = target?.closest?.('#deck-settings-advanced');
      if (advanced) advanced.open = true;
      const scroller = target?.closest?.('.settings');
      if (scroller) {
        const nav = scroller.querySelector('#deck-settings-nav');
        scroller.scrollBy({ top: target.getBoundingClientRect().top - scroller.getBoundingClientRect().top - (scroller.classList?.contains('capy-settings') ? 0 : nav?.offsetHeight || 0) - 12 });
        selectSettings(scroller, jump.dataset.deckSetting);
      }
      target?.setAttribute?.('tabindex', '-1');
      target?.focus?.({ preventScroll: true });
    }
  });
  const drawn = (render) => (options = {}) => deckOn(storage) ? render({ ...shell.options(), ...options, screens }) : '';
  return { appearance, shell, policyPresentation: () => deckOn(storage) ? capyPolicyPresentation : null, on: () => deckOn(storage), home: () => deckHome(screens), owns, show, leave, settings, choose, toggle, bar: drawn(renderDeckBar), top: drawn(renderDeckTop), tabs: drawn(options => renderDeckTabs(options) + renderDeckMore(options)) };
}
