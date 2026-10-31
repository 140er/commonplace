import { escape as e } from "./api.js";

const languages = ["en", "nl"], storageKey = "commonplace-voice-language";
const variants = ["a", "b", "c"], variantKey = "commonplace-voice-ui";
const inputs = 'form[data-form="space-message"] textarea[data-draft], form[data-form="comment"] input[data-draft], form[data-form="reply"] input[data-draft]';
const mic = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11v1a7 7 0 0 0 14 0v-1M12 19v3M8 22h8"/></svg>';

// Blocky glyph for the A/B/C variants; the default keeps the outline mic above.
const block = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M9 2h6v10H9z"/><path d="M5 9h2.5v4.5h9V9H19v7h-5.75v3H16v3H8v-3h2.75v-3H5z"/></svg>';

// Design trial: ?voice_ui=a|b|c picks a variant and remembers it; any other
// value (e.g. ?voice_ui=default) returns to the shipped design.
export function voiceVariant(search, storage) {
  let ui = "";
  try { ui = storage?.getItem(variantKey) || ""; } catch { /* Storage may be blocked. */ }
  const asked = new URLSearchParams(search || "").get("voice_ui");
  if (asked !== null) {
    ui = variants.includes(asked) ? asked : "";
    try { if (ui) storage?.setItem(variantKey, ui); else storage?.removeItem(variantKey); } catch { /* Keep it for this page. */ }
  }
  return variants.includes(ui) ? ui : "";
}

export function languagePicker(storage) {
  let language = "en";
  try { const saved = storage?.getItem(storageKey); if (languages.includes(saved)) language = saved; } catch { /* Storage may be blocked. */ }
  return {
    get: () => language,
    set(value) { if (!languages.includes(value)) return; language = value; try { storage?.setItem(storageKey, value); } catch { /* Keep the in-memory choice. */ } },
  };
}

// Area averaging handles fractional 44.1 kHz frames as well as 48 kHz. The
// wire format is explicitly little-endian, regardless of the device's endian.
export function pcm16(samples, rate) {
  const count = Math.min(960000, Math.floor(samples.length * 16000 / rate));
  const buffer = new ArrayBuffer(count * 2), view = new DataView(buffer), step = rate / 16000;
  for (let i = 0; i < count; i++) {
    const start = i * step, end = (i + 1) * step;
    let sum = 0;
    for (let j = Math.floor(start); j < end; j++) sum += samples[j] * (Math.min(j + 1, end) - Math.max(j, start));
    const value = Math.max(-1, Math.min(1, sum / step));
    view.setInt16(i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
  }
  return buffer;
}

export function insertTranscript(input, text, EventType = Event) {
  if (!text) return;
  const caret = input.selectionStart ?? input.value.length;
  const left = input.value.slice(0, caret), right = input.value.slice(caret);
  const added = `${/\S$/.test(left) && /^\S/.test(text) ? " " : ""}${text}${/\S$/.test(text) && /^\S/.test(right) ? " " : ""}`;
  // Insert without replacing even selected text: everything already typed stays.
  input.value = left + added + right;
  input.setSelectionRange(caret + added.length, caret + added.length);
  input.dispatchEvent(new EventType("input", { bubbles: true }));
}

export function voiceInput(root, api, failed, browser = window) {
  let storage; try { storage = browser.localStorage; } catch { /* Private browsing. */ }
  const picker = languagePicker(storage), ui = voiceVariant(browser.location?.search, storage), jobs = new Map(), carets = new Map();
  let config = { available: false, enabled: false, owner: false }, menu = null, generation = 0, saving = false;
  const scope = (input) => input.closest("#card-panel, #content")?.id || "";
  const identity = (input) => `${scope(input)}:${input.dataset.draft}`;
  const find = (job) => [...root.querySelectorAll(inputs)].find((input) => input.dataset.draft === job.key && scope(input) === job.scope);
  function remember() {
    root.querySelectorAll(inputs).forEach((input) => carets.set(identity(input), [input.selectionStart, input.selectionEnd]));
  }
  function view() {
    const page = root.querySelector(".settings"), row = page?.querySelector("[data-voice-settings]");
    if (page && !row && config.owner && config.available) page.insertAdjacentHTML("afterbegin", settings());
    if (row && !(config.owner && config.available)) row.remove();
    for (const [key, job] of jobs) if (!find(job)) { job.controller?.abort(); job.flushed?.(); release(job); jobs.delete(key); }
    root.querySelectorAll(inputs).forEach((input) => {
      const form = input.form, key = input.dataset.draft, job = jobs.get(key);
      let control = form.querySelector(".voice-control");
      if (!config.available || !config.enabled) { control?.remove(); return; }
      if (!control) { control = browser.document.createElement("div"); control.className = "voice-control"; form.querySelector('button:not([type="button"])').before(control); }
      const phase = job?.phase || "idle", busy = phase === "starting" || phase === "transcribing";
      const label = phase === "recording" ? "Stop recording" : busy ? (phase === "starting" ? "Starting microphone" : "Transcribing") : "Record voice";
      control.dataset.state = phase;
      control.setAttribute("aria-busy", String(busy));
      const focused = control.contains?.(browser.document.activeElement) ? { ...browser.document.activeElement.dataset } : null;
      if (ui) control.dataset.ui = ui;
      const glyph = phase === "recording" ? '<span class="voice-stop" aria-hidden="true"></span>' : busy ? '<span class="status-dot checks" aria-hidden="true"></span>' : ui ? block : mic;
      const word = ui === "b" ? `<span class="voice-word" aria-hidden="true">${phase === "recording" ? "Stop" : busy ? "Wait" : "Talk"}</span>` : "";
      const meter = ui === "c" && phase === "recording" ? `<span class="voice-meter" aria-hidden="true">${"<i></i>".repeat(8)}</span>` : "";
      control.innerHTML = `<button type="button" data-voice="mic" aria-label="${e(label)}" aria-pressed="${phase === "recording"}" ${busy || (!job && jobs.size) ? "disabled" : ""}>${glyph}${word}${meter}</button><button type="button" data-voice="language" aria-label="Voice language" aria-haspopup="menu" aria-expanded="${menu === identity(input)}" ${jobs.size ? "disabled" : ""}>${e(picker.get().toUpperCase())}</button>${menu === identity(input) ? `<div class="voice-menu" role="menu" aria-label="Voice language">${languages.map((lang) => `<button type="button" data-voice="choose" data-language="${e(lang)}" role="menuitemradio" aria-checked="${picker.get() === lang}">${e(lang.toUpperCase())}</button>`).join("")}</div>` : ""}`;
      if (focused) control.querySelector(`[data-voice="${e(focused.voice)}"]${focused.language ? `[data-language="${e(focused.language)}"]` : ""}`)?.focus({ preventScroll: true });
      const caret = carets.get(identity(input));
      if (caret && job && browser.document.activeElement !== input) input.setSelectionRange(...caret);
    });
    root.querySelectorAll('[data-voice="enabled"]').forEach((button) => { button.textContent = config.enabled ? "On" : "Off"; button.setAttribute("aria-pressed", String(config.enabled)); button.disabled = saving; });
  }
  // Variant C's meter: eight blocks from -60 dBFS to -12 dBFS, about 10 updates a second.
  function level(job, chunk) {
    const now = Date.now();
    if (now - (job.metered || 0) < 100) return;
    job.metered = now;
    let sum = 0; for (const sample of chunk) sum += sample * sample;
    const db = 10 * Math.log10(sum / chunk.length + 1e-10);
    const meter = find(job)?.form.querySelector(".voice-meter");
    if (meter) meter.dataset.level = String(Math.max(0, Math.min(8, Math.round((db + 60) / 6))));
  }
  function settings() {
    return config.owner && config.available ? `<section class="settings-section" data-voice-settings><div class="setting-row"><span>Voice</span><button type="button" data-voice="enabled" aria-label="Voice" aria-pressed="${config.enabled}">${config.enabled ? "On" : "Off"}</button></div></section>` : "";
  }
  async function load() {
    const current = ++generation;
    try { const next = await api("/api/voice"); if (current === generation) { config = { ...config, ...next }; view(); } }
    catch (error) { if (current === generation) failed(error.message); }
  }
  function release(job) {
    browser.clearTimeout(job.timer);
    if (job.node) job.node.port.onmessage = null;
    job.node?.disconnect(); job.source?.disconnect();
    job.stream?.getTracks().forEach((track) => track.stop());
    if (job.context && job.context.state !== "closed") void job.context.close().catch(() => {});
    job.node = job.source = job.stream = job.context = null;
    job.chunks = []; job.length = 0;
  }
  function stop() {
    for (const job of jobs.values()) { job.controller?.abort(); job.flushed?.(); release(job); }
    jobs.clear(); menu = null; view();
  }
  function reset() { generation++; stop(); config = { available: false, enabled: false, owner: false }; carets.clear(); view(); }
  function fail(job, message) {
    if (jobs.get(job.key) !== job) return;
    release(job); jobs.delete(job.key); view(); failed(message);
  }
  async function finish(job) {
    if (job.phase !== "recording") return;
    job.phase = "transcribing"; browser.clearTimeout(job.timer); view();
    try {
      // Flush the worklet's final partial block before closing the capture graph.
      await new Promise((resolve) => {
        const timeout = browser.setTimeout(resolve, 1000);
        job.flushed = () => { browser.clearTimeout(timeout); resolve(); };
        job.node.port.postMessage("stop");
      });
      if (jobs.get(job.key) !== job) return;
      const samples = new Float32Array(job.length);
      let offset = 0; for (const chunk of job.chunks) { samples.set(chunk, offset); offset += chunk.length; }
      const body = pcm16(samples, job.context.sampleRate);
      release(job);
      if (!body.byteLength) throw new Error("No audio was recorded.");
      job.controller = new browser.AbortController();
      const response = await browser.fetch(`/api/voice/transcribe?lang=${encodeURIComponent(job.language)}`, { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/octet-stream", "Accept": "application/json" }, body, signal: job.controller.signal });
      const payload = await response.json();
      if (jobs.get(job.key) !== job) return;
      if (response.status === 401) browser.document.dispatchEvent(new browser.Event("session-expired"));
      if (!response.ok) throw new Error(payload.error || "Could not transcribe the recording.");
      const input = find(job);
      if (input) { insertTranscript(input, payload.text, browser.Event); carets.set(identity(input), [input.selectionStart, input.selectionEnd]); }
      jobs.delete(job.key); view();
    } catch (error) { fail(job, error.message); }
  }
  async function toggle(input) {
    const key = input.dataset.draft, existing = jobs.get(key);
    if (existing) { await finish(existing); return; }
    if (jobs.size || !config.available || !config.enabled) return;
    remember(); menu = null;
    const job = { key, scope: scope(input), language: picker.get(), phase: "starting", chunks: [], length: 0 };
    jobs.set(key, job); view();
    try {
      const Context = browser.AudioContext || browser.webkitAudioContext;
      if (!browser.navigator.mediaDevices?.getUserMedia || !Context || !browser.AudioWorkletNode) throw new Error("Microphone recording is unavailable. Use HTTPS or localhost.");
      job.context = new Context(); await job.context.resume();
      if (jobs.get(key) !== job) return;
      const stream = await browser.navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
      if (jobs.get(key) !== job) { stream.getTracks().forEach((track) => track.stop()); return; }
      job.stream = stream;
      await job.context.audioWorklet.addModule("/app/voice-worklet.js");
      if (jobs.get(key) !== job) return;
      job.node = new browser.AudioWorkletNode(job.context, "commonplace-voice", { channelCount: 1, channelCountMode: "explicit" });
      job.node.port.onmessage = ({ data }) => {
        if (data === "stopped") { job.flushed?.(); return; }
        if (jobs.get(key) !== job) return;
        const remaining = Math.max(0, Math.floor(job.context.sampleRate * 60) - job.length);
        const chunk = data.length <= remaining ? data : data.slice(0, remaining);
        if (chunk.length) { job.chunks.push(chunk); job.length += chunk.length; }
        if (ui === "c" && chunk.length) level(job, chunk);
        if (job.length >= job.context.sampleRate * 60) void finish(job);
      };
      job.source = job.context.createMediaStreamSource(stream);
      job.source.connect(job.node); job.node.connect(job.context.destination);
      job.phase = "recording";
      job.timer = browser.setTimeout(() => { void finish(job); }, 60000);
      view();
    } catch (error) { fail(job, error.name === "NotAllowedError" ? "Microphone access was denied." : error.message); }
  }
  root.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-voice]");
    if (!button) { if (menu) { menu = null; view(); } return; }
    event.preventDefault();
    const action = button.dataset.voice, form = button.closest("form"), input = form?.querySelector("[data-draft]");
    if (action === "mic") await toggle(input);
    else if (action === "language") { menu = menu === identity(input) ? null : identity(input); view(); form.querySelector('[role="menuitemradio"][aria-checked="true"]')?.focus(); }
    else if (action === "choose") { picker.set(button.dataset.language); menu = null; view(); form.querySelector('[data-voice="language"]')?.focus(); }
    else if (action === "enabled" && !saving) {
      const current = generation;
      saving = true; view();
      try { const next = await api("/api/voice", { method: "PATCH", body: { enabled: !config.enabled } }); if (current === generation) { config = { ...config, ...next }; if (!config.enabled) stop(); } }
      catch (error) { if (current === generation) failed(error.message); }
      finally { saving = false; view(); }
    }
  });
  root.addEventListener("keydown", (event) => {
    if (!event.target.closest(".voice-control")) return;
    event.stopPropagation();
    if (event.key === "Escape") { const form = event.target.closest("form"); menu = null; view(); form?.querySelector('[data-voice="language"]')?.focus(); }
    if (["ArrowDown", "ArrowUp"].includes(event.key)) {
      const items = [...event.target.closest(".voice-control").querySelectorAll('[role="menuitemradio"]')];
      if (items.length) { event.preventDefault(); items[(items.indexOf(event.target) + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus(); }
    }
  });
  return { load, reset, stop, view, remember, settings };
}
