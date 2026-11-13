// Person-scoped Deck appearance. It has no effects until an authenticated Deck shell mounts.
import { StageDither, SCENES } from './deck-dither.js';

export const DEFAULTS = Object.freeze({ scene: 'capy-cyber', bayer: 4, vignette: .55, dot: 4, surface: .94 });
const LABELS = { 'capy-cyber': 'Capy Cyber', 'palace-gate': 'Palace Gate', 'nebula-quiet': 'Nebula Quiet' };
const clamp = (v, fallback, min, max) => typeof v === 'number' && Number.isFinite(v) ? Math.max(min, Math.min(max, v)) : fallback;
function normalize(value) {
  const v = value && typeof value === 'object' ? value : {};
  return { scene: Object.hasOwn(SCENES, v.scene) ? v.scene : DEFAULTS.scene,
    bayer: v.bayer === 8 ? 8 : 4, vignette: clamp(v.vignette, DEFAULTS.vignette, 0, 1),
    dot: Math.round(clamp(v.dot, DEFAULTS.dot, 3, 7)), surface: clamp(v.surface, DEFAULTS.surface, .6, 1) };
}
const storageOrNull = () => { try { return globalThis.localStorage; } catch { return null; } };
export function appearanceStore(me = {}, storage = storageOrNull()) {
  const person = me?.actor || me?.user;
  const key = person ? `cmmn.deck.appearance.${encodeURIComponent(me.workspace || '')}.${encodeURIComponent(person)}` : null;
  let current = { ...DEFAULTS };
  return { key,
    read() { try { const saved = key && storage?.getItem(key); if (saved) current = normalize(JSON.parse(saved)); } catch { /* Private storage is optional. */ } return { ...current }; },
    write(value) { current = normalize(value); try { if (key) storage?.setItem(key, JSON.stringify(current)); } catch { /* Retain it for this visit. */ } return { ...current }; },
  };
}

export function appearanceHTML(value) {
  const v = normalize(value);
  const slider = (key, label, min, max, step, suffix, shown) => `<label class="appearance-slider">${label}<output data-appearance-output="${key}">${shown}${suffix}</output><input type="range" aria-label="${label}" data-appearance="${key}" min="${min}" max="${max}" step="${step}" value="${v[key]}"></label>`;
  return `<section class="settings-section deck-appearance" data-deck-appearance aria-labelledby="appearance-title">
    <h2 id="appearance-title">Appearance</h2><p>Changes apply to the whole app straight away.</p>
    <div class="appearance-preview"><canvas data-appearance-preview aria-label="Live scene preview"></canvas><div class="appearance-sample" aria-hidden="true"></div></div>
    <div class="appearance-scenes" role="group" aria-label="Scene">${Object.keys(SCENES).map(scene => `<button type="button" data-scene="${scene}" aria-pressed="${v.scene === scene}"><canvas aria-hidden="true"></canvas><span>${LABELS[scene]}</span></button>`).join('')}</div>
    <div class="appearance-pattern"><span>Dither pattern<small>4×4 is coarser, 8×8 finer.</small></span><div role="group" aria-label="Bayer pattern">${[4, 8].map(n => `<button type="button" data-bayer="${n}" aria-pressed="${v.bayer === n}">${n}×${n}</button>`).join('')}</div></div>
    ${slider('vignette', 'Vignette', 0, 1, .01, '%', Math.round(v.vignette * 100))}
    ${slider('surface', 'Surface opacity', .6, 1, .01, '%', Math.round(v.surface * 100))}
    ${slider('dot', 'Dot size', 3, 7, 1, ' px', v.dot)}
    <p class="appearance-save">Saved for you in this browser.</p>
  </section>`;
}

export function createDeckAppearance({ root, context, on, doc = globalThis.document, storage = storageOrNull() }) {
  let stage = null, renderer = null, preview = null, thumbnails = [], panel = null, store = null, personKey = null;
  let value = { ...DEFAULTS }, base = '#/';
  const home = () => base === '#/' || base === '#/home';
  function loadPerson() {
    const next = appearanceStore(context?.()?.me, storage);
    if (next.key !== personKey || !store) { store = next; personKey = next.key; value = store.read(); return true; }
    return false;
  }
  function apply() {
    doc.documentElement.style.setProperty('--surface-a', String(value.surface));
    if (renderer) renderer.set({ ...value, dim: home() ? 0 : .45 });
    stage?.setAttribute('data-dimmed', String(!home()));
  }
  function clearPreviews() {
    preview?.destroy(); preview = null;
    thumbnails.forEach(item => item.destroy()); thumbnails = []; panel = null;
  }
  function stop() {
    renderer?.destroy(); renderer = null; stage?.remove(); stage = null;
    const main = panel?.parentElement?.querySelector(':scope > .deck-settings-main');
    panel?.remove();
    if (main) main.replaceWith(...main.childNodes);
    clearPreviews();
    doc?.documentElement?.style?.removeProperty('--surface-a');
  }
  function sync(route = '#/') {
    base = route;
    if (!on()) { stop(); return; }
    const center = root?.querySelector?.('.center');
    if (!center || !doc?.createElement) return;
    const personChanged = loadPerson();
    // Set the saved alpha before the shell paints; construct with the saved scene on the first pass.
    doc.documentElement.style.setProperty('--surface-a', String(value.surface));
    if (panel && !panel.isConnected) clearPreviews();
    if (!stage?.isConnected || stage.parentElement !== center) {
      renderer?.destroy(); stage?.remove();
      stage = doc.createElement('div'); stage.className = 'deck-stage';
      stage.setAttribute('aria-hidden', 'true'); stage.setAttribute('data-dimmed', String(!home()));
      const canvas = doc.createElement('canvas'); canvas.className = 'stage-dither';
      stage.append(canvas); center.prepend(stage);
      renderer = new StageDither(canvas, { ...value, dim: home() ? 0 : .45 });
    } else if (personChanged || renderer.opts.dim !== (home() ? 0 : .45) || renderer.opts.scene !== value.scene) apply();
  }
  function settings(box) {
    if (!on() || !box?.ownerDocument) return;
    sync(base); clearPreviews();
    box.querySelector('[data-deck-appearance]')?.remove();
    box.insertAdjacentHTML('beforeend', appearanceHTML(value));
    panel = box.querySelector('[data-deck-appearance]');
    const main = box.querySelector(':scope > .deck-settings-main') || doc.createElement('div');
    main.className = 'deck-settings-main';
    [...box.children].filter(node => node !== panel && node !== main).forEach(node => main.append(node));
    box.prepend(main);
    preview = new StageDither(panel.querySelector('[data-appearance-preview]'), { ...value, dim: 0, twinkle: false });
    thumbnails = [...panel.querySelectorAll('[data-scene]')].map(button => new StageDither(button.querySelector('canvas'), { ...value, dot: 3, scene: button.dataset.scene, dim: 0, twinkle: false }));
    function update(patch) {
      value = store.write({ ...value, ...patch }); apply(); preview.set(value);
      for (const [key, label] of Object.entries({ vignette: `${Math.round(value.vignette * 100)}%`, surface: `${Math.round(value.surface * 100)}%`, dot: `${value.dot} px` })) panel.querySelector(`[data-appearance-output="${key}"]`).textContent = label;
      panel.querySelectorAll('[data-scene]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.scene === value.scene)));
      panel.querySelectorAll('[data-bayer]').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.bayer) === value.bayer)));
      if ('bayer' in patch || 'vignette' in patch) thumbnails.forEach(item => item.set({ bayer: value.bayer, vignette: value.vignette }));
    }
    panel.addEventListener('click', event => {
      const scene = event.target.closest('[data-scene]'), bayer = event.target.closest('[data-bayer]');
      if (scene) update({ scene: scene.dataset.scene });
      if (bayer) update({ bayer: Number(bayer.dataset.bayer) });
    });
    panel.addEventListener('input', event => { const key = event.target.dataset.appearance; if (['vignette', 'surface', 'dot'].includes(key)) update({ [key]: Number(event.target.value) }); });
  }
  return { sync, settings, stop, measurement: () => renderer?.measurement };
}
