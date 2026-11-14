import { api as request, escape as e } from "./api.js";
import { importConsent } from "./deck-memory.js";

export const MAX_FILE_BYTES = 4 * 1024 * 1024;
const formats = [
  ["chatgpt", "auto", "ChatGPT conversation (.json)"],
  ["claude-ai", "auto", "Claude conversation (.json)"],
  ["cursor", "messages", "Conversation messages (.json)"],
  ["claude-code", "auto", "Claude Code transcript (.jsonl)"],
  ["codex", "auto", "Codex transcript (.jsonl)"],
  ["t3", "auto", "Native conversation transcript (.jsonl)"],
  ["grok-bot", "auto", "Grok memory facts (.jsonl)"],
  ["native-memory", "file", "Memory text (.md or .txt)"],
];
const scopeLabel = (scope) => scope === "private" ? "Your private imported history" : "Workspace conversations";
const plainError = (error) => String(error || "").replace(/(?:you can )?retry safely\.?/gi, "Nothing was saved twice. Try again.");

export function renderImport(state, me = {}, look = "classic") {
  const deck = look === "deck";
  const { phase = "choose", preview, report, error } = state;
  const heading = { choose: deck ? "Import memory files" : "Import memory", reading: "Reading file", previewing: "Checking file", preview: "Review import", importing: "Importing memory", report: "Import report", error: deck ? "Import did not finish" : "Import needs attention" }[phase];
  const busy = ["reading", "previewing", "importing"].includes(phase);
  const summary = preview ? `<dl class="memory-import-summary"><div><dt>File</dt><dd>${e(state.filename)}</dd></div><div><dt>Source</dt><dd>${e(state.sourceLabel || formats.find(([source, format]) => source === state.source && format === state.format)?.[2] || preview.source)}</dd></div><div><dt>${deck ? "Save into" : "Destination"}</dt><dd>${e(scopeLabel(preview.scope))}</dd></div>${deck && phase === "report" ? "" : `<div><dt>Count</dt><dd>${preview.total} file · ${preview.messages} ${preview.source === "grok-bot" ? (preview.messages === 1 ? "fact" : "facts") : (preview.messages === 1 ? "message" : "messages")}</dd></div><div><dt>Size</dt><dd>${preview.source_bytes.toLocaleString()} bytes</dd></div>`}</dl>` : "";
  return `<section class="memory-import" aria-labelledby="memory-import-title" aria-busy="${busy}">
    <header><h1 id="memory-import-title" tabindex="-1">${heading}</h1></header>
    ${phase === "choose" ? (deck ? `<p class="memory-import-intro">Bring in old chats. Each message is saved with your exact words.</p><p class="memory-import-hint">Each memory is marked with the file it came from.</p><form data-import-choose>
      <div class="memory-import-group"><label for="import-format">Where it comes from</label><select id="import-format" name="format">${formats.map(([source, , label], index) => source === "native-memory" && me.can_admin !== true ? "" : `<option value="${index}">${e(label)}</option>`).join("")}</select></div>
      <div class="memory-import-group"><label for="import-scope">Save into</label><select id="import-scope" name="scope" aria-describedby="import-scope-hint"><option value="threads">Workspace conversations</option>${me.can_admin === true ? '<option value="private">Your private imported history</option>' : ""}</select><p id="import-scope-hint" class="memory-import-hint">Everyone who can read this workspace's conversations will see these. For private history, import in your personal workspace.</p></div>
      <div class="memory-import-group"><label for="import-file">File</label><div class="memory-import-file"><span class="memory-import-picker"><span class="btn">Choose file</span><input id="import-file" name="file" type="file" accept=".json,.jsonl,.ndjson,.md,.txt" aria-describedby="import-file-hint" required></span><span data-import-filename aria-live="polite">No file chosen</span></div><p id="import-file-hint" class="memory-import-hint">One text file, up to 4 MB. No ZIP files or attachments.</p></div>
      <div class="memory-import-actions"><button class="btn" type="button" data-import-action="close">Cancel</button><button class="btn primary" type="submit">Preview file</button></div>
      </form>` : `<p>Choose one export. Your exact words are kept.</p><form data-import-choose>
      <label>Export format<select name="format">${formats.map(([source, , label], index) => source === "native-memory" && me.can_admin !== true ? "" : `<option value="${index}">${e(label)}</option>`).join("")}</select></label>
      <label>Destination<select name="scope"><option value="threads">Workspace conversations</option>${me.can_admin === true ? '<option value="private">Your private imported history</option>' : ""}</select></label>
      <p class="memory-import-hint">Workspace access rules apply. Private history requires your personal workspace.</p>
      <label>Export file<input name="file" type="file" accept=".json,.jsonl,.ndjson,.md,.txt" required></label>
      <p class="memory-import-hint">UTF-8, up to 4 MiB. One conversation, facts or memory-text file. No ZIPs or attachments.</p>
      <div class="memory-import-actions"><button class="btn primary" type="submit">Preview file</button><button class="btn" type="button" data-import-action="close">Cancel</button></div>
      </form>`) : ""}
    ${busy ? `<p role="status">${phase === "importing" ? "Saving 1 file. Keep this screen open for the report." : "Checking the export before anything is saved."}</p>${deck ? '<div class="memory-import-progress">' : ""}<progress aria-label="${phase === "importing" ? "Importing file" : "Checking file"}"></progress>${deck ? "</div>" : ""}${summary}` : ""}
    ${phase === "preview" ? `${summary}<p>No memory has been saved yet. Confirm to keep this file word for word in ${e(scopeLabel(preview.scope).toLowerCase())}.</p><p class="memory-import-hint">${deck ? "The file stays on this server. No AI model reads it." : "The file is processed by this instance without models or external services."} Importing the same file to the same destination again adds nothing.</p>${deck ? `<p class="memory-import-hint">Each memory is marked with the file it came from.</p>` : ""}${deck ? `${importConsent(state)}<div class="memory-import-actions"><button class="btn ghost" data-import-action="choose">Choose another file</button><button class="btn" data-import-action="close">Cancel</button><button class="btn primary" data-import-action="confirm"${!state.consent ? " disabled" : ""}>Import</button></div>` : '<div class="memory-import-actions"><button class="btn primary" data-import-action="confirm">Confirm import</button><button class="btn" data-import-action="close">Cancel</button><button class="btn ghost" data-import-action="choose">Choose another file</button></div>'}` : ""}
    ${phase === "error" ? `<div role="alert"><p>${e(deck ? plainError(error) : error)}</p></div>${summary}<div class="memory-import-actions">${preview ? '<button class="btn primary" data-import-action="retry">Retry import</button>' : ""}<button class="btn" data-import-action="choose">Choose another file</button><button class="btn ghost" data-import-action="close">Cancel</button></div>` : ""}
    ${phase === "report" ? `<p role="status">${report.errors ? (deck ? "Nothing was saved twice. Try again." : "The file could not be saved. You can retry safely.") : report.duplicates ? "Already imported. No new copy was added." : "Import complete."}</p>${summary}<dl class="memory-import-summary">${deck ? "" : `<div><dt>Files processed</dt><dd>${report.processed} of ${report.total}</dd></div>`}<div><dt>${report.accepted !== undefined ? "Accepted (including exact retries)" : "Added"}</dt><dd>${report.accepted ?? report.imported}</dd></div>${report.duplicates !== undefined ? `<div><dt>Already imported</dt><dd>${report.duplicates}</dd></div>` : ""}${deck && !report.errors ? "" : `<div><dt>Errors</dt><dd>${report.errors}</dd></div>`}</dl>${report.results.filter((row) => row.status === "error").map((row) => `<p role="alert">${e(deck ? plainError(row.error) : row.error)}</p>`).join("")}<div class="memory-import-actions">${report.errors ? '<button class="btn primary" data-import-action="retry">Retry import</button>' : '<button class="btn primary" data-import-action="close">Done</button>'}<button class="btn" data-import-action="choose">Import another file</button></div>` : ""}
    </section>`;
}

export function createMemoryImport({ mount, me, api = request, onClose = () => {}, look = mount.ownerDocument?.documentElement?.dataset.ui || "classic" }) {
  let state = { phase: "choose" }, upload = null, stopped = false, epoch = 0;
  function draw() {
    if (stopped) return;
    mount.innerHTML = renderImport(state, me, look);
    mount.querySelector("#memory-import-title")?.focus();
  }
  function reset() { ++epoch; upload = null; state = { phase: "choose" }; draw(); }
  async function choose(event) {
    if (!event.target.matches("[data-import-choose]")) return;
    event.preventDefault();
    if (state.phase !== "choose") return;
    const form = event.target, file = form.elements.file.files[0];
    if (!file) return;
    const [source, format, sourceLabel] = formats[Number(form.elements.format.value)];
    const privateScope = form.elements.scope.value === "private";
    const visit = ++epoch;
    upload = null;
    state = { phase: "reading", filename: file.name, source, format, ...(look === "deck" ? { sourceLabel } : {}) };
    draw();
    try {
      if (file.size > MAX_FILE_BYTES) throw new Error("size");
      if (!(source === "native-memory" ? /\.(md|txt)$/i : /\.(json|jsonl|ndjson)$/i).test(file.name)) throw new Error("type");
      // Keep BOMs and CR/LF exactly; invalid UTF-8 is refused, never replaced.
      const transcript = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer());
      if (stopped || visit !== epoch) return;
      const body = { filename: file.name, source, format, transcript, ...(privateScope ? { private: true } : {}) };
      state.phase = "previewing"; draw();
      const preview = await api("/api/memory/import/preview", { method: "POST", body });
      if (stopped || visit !== epoch) return;
      upload = body;
      state = { ...state, phase: "preview", preview };
    } catch (error) {
      if (stopped || visit !== epoch) return;
      state = { ...state, phase: "error", error: error.payload?.error || (file.size > MAX_FILE_BYTES ? "Choose a file of 4 MiB or less." : "Could not read this export. Choose a UTF-8 file that matches the selected format.") };
    }
    draw();
  }
  async function confirm() {
    if (!upload || !["preview", "error", "report"].includes(state.phase) || (look === "deck" && !state.consent)) return;
    const visit = ++epoch;
    state.phase = "importing"; state.error = ""; draw();
    try {
      const report = await api("/api/memory/import/confirm", { method: "POST", body: { ...upload, preview: state.preview.preview, consent: true } });
      if (stopped || visit !== epoch) return;
      state = { ...state, phase: "report", report };
      if (!report.errors) upload = null;
    } catch (error) {
      if (stopped || visit !== epoch) return;
      state = { ...state, phase: "error", error: error.payload?.error || "Could not confirm the import. Retry sends the same file and adds no duplicate." };
    }
    draw();
  }
  function click(event) {
    const action = event.target.closest("[data-import-action]")?.dataset.importAction;
    if (["reading", "previewing", "importing"].includes(state.phase)) return;
    if (action === "choose") reset();
    if (action === "close") { upload = null; onClose(); }
    if (["confirm", "retry"].includes(action)) void confirm();
  }
  function change(event) {
    if (look === "deck" && typeof event.target.checked === "boolean" && event.target.matches("[data-import-consent]")) {
      state.consent = event.target.checked === true;
      const button = mount.querySelector('[data-import-action="confirm"]');
      if (button) button.disabled = !state.consent;
      return;
    }
    if (!event.target.matches('input[type="file"]')) return;
    const filename = mount.querySelector("[data-import-filename]");
    if (filename) filename.textContent = event.target.files[0]?.name || "No file chosen";
  }
  mount.addEventListener("submit", choose);
  mount.addEventListener("click", click);
  mount.addEventListener("change", change);
  draw();
  return { stop() { stopped = true; ++epoch; upload = null; state = {}; mount.removeEventListener("submit", choose); mount.removeEventListener("click", click); mount.removeEventListener("change", change); } };
}
