import { api as request, escape as e } from "./api.js";

export const NOTE_MAX_LENGTH = 100000;
const exact = text => e(text).replaceAll("\r", "&#13;");
export const noteProblem = (text = "") => !text.trim() ? "Write a note first." : [...text].length > NOTE_MAX_LENGTH ? "Use up to 100,000 characters." : /\u0000/u.test(text) ? "Remove invalid characters from the note." : "";

export async function saveNote({ text, wing, room = "general" }, api = request) {
  const problem = noteProblem(text);
  if (problem) throw new Error(problem);
  return api("/api/memory", { method: "POST", body: { text, theme: wing, topic: room } });
}

export function renderNote(state = {}) {
  if (!state.open) return "";
  const { choices, loading, saving, saved, text = "", wing, room = "general", error } = state;
  const wings = choices?.wings || [];
  const picked = wings.find(row => row.wing === wing);
  const label = value => wings.find(row => row.wing === value)?.name || value;
  return `<section class="memory-note" aria-labelledby="memory-note-title" aria-busy="${Boolean(loading || saving)}">
    <h2 id="memory-note-title">Add a note</h2><p>Save a workspace note, separate from bot and conversation memory. Your words stay exactly as typed.</p>
    ${loading ? '<p role="status">Loading places to save…</p>' : ""}
    ${error ? `<p tabindex="-1" role="alert">${e(error)}</p>` : ""}
    ${saved ? `<p class="memory-note-saved" tabindex="-1" role="status">Saved to ${e(label(saved.theme))}. <a href="#/memory?drawer=${encodeURIComponent(saved.id)}" data-note-drawer="${e(saved.id)}">Open drawer</a>. ${saved.notice ? e(saved.notice) : "It will be searchable shortly."}</p>` : ""}
    ${choices ? `<form data-memory-note><fieldset${saving ? " disabled" : ""}>
      <label for="note-text">Note</label><textarea id="note-text" name="note-text" rows="5" aria-describedby="note-limit">${exact(text)}</textarea><p id="note-limit">Up to 100,000 characters. Nothing is summarised or rewritten.</p>
      <details class="memory-note-preview"><summary>Preview exact words</summary><pre data-note-preview>${exact(text)}</pre></details>
      <div class="memory-note-destination"><label>Save to<select name="note-wing">${wings.map(row => `<option value="${e(row.wing)}"${row.wing === wing ? " selected" : ""}>${e(row.name)}</option>`).join("")}</select></label><label>Room<select name="note-room">${(picked?.rooms || ["general"]).map(value => `<option value="${e(value)}"${value === room ? " selected" : ""}>${e(value)}</option>`).join("")}</select></label></div>
      <p>${String(wing).startsWith("private") ? "This is private memory. Only people allowed to read this wing can see it." : "People who can read this wing can find this note."}</p>
      <div class="memory-note-actions"><button type="button" class="btn ghost" data-memory-action="note-close">Close</button><button class="btn primary" type="submit">${saving ? "Saving…" : "Save"}</button></div>
    </fieldset></form>` : !loading ? '<button class="btn" data-memory-action="note">Try again</button><button class="btn ghost" data-memory-action="note-close">Close</button>' : ""}</section>`;
}

export function createNote({ api = request, draw, alive, boards = () => [], onResult = () => {} }) {
  const state = { open: false, text: "", room: "general" };
  return {
    state,
    async open() {
      state.open = true;
      if (state.loading || state.choices) { draw(); return; }
      state.loading = true; state.error = ""; draw();
      try {
        const page = await api("/api/memory/rooms?compose=1", { timeoutMs: 10000 });
        if (!alive()) return;
        if (!page.composer) { state.error = "You cannot save notes in this workspace."; return; }
        state.choices = { ...page.composer, wings: page.composer.wings.map(row => ({ ...row, name: boards().find(board => board.wing === row.wing)?.name || row.name })) };
        state.wing = state.choices.default_wing;
      } catch (error) { if (alive()) state.error = plainError(error); }
      finally { state.loading = false; if (alive()) draw(); }
    },
    async save() {
      if (state.saving || !state.choices) return;
      state.error = noteProblem(state.text); state.saved = null;
      if (state.error) { draw(); onResult(); return; }
      state.saving = true; draw();
      try {
        const saved = await saveNote(state, api);
        if (alive()) { state.saved = saved; state.text = ""; }
      } catch (error) { if (alive()) state.error = plainError(error); }
      finally { state.saving = false; if (alive()) { draw(); onResult(); } }
    },
  };
}

function plainError(error) {
  if (error?.status === 507 || error?.payload?.code === "storage_full") return "Storage is low, so nothing was saved. Free some space and try again.";
  if (error?.status === 503) return `Memory is busy. Nothing was saved. Try again${Number(error.payload?.retry_after_s) > 0 ? ` in about ${Number(error.payload.retry_after_s)} seconds` : " in a moment"}.`;
  if ([401, 403].includes(error?.status)) return "You cannot save a note here. Check your access and try again.";
  return error?.status === 400 ? error.message : "The note could not be saved. Your text is still here; try again.";
}
