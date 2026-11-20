import { api as request, escape as e, elapsed } from "./api.js";

// "Review comments" in a project's settings, for the workspace owner: whether the project's
// Coworker answers review comments on its cards' pull requests (off until switched on), how many
// rounds it gets before it asks, whether it may post its replies on public repositories, who
// counts as a trusted reviewer, and whether the comment reader is set up. Reads come from
// GET /api/pr-review/boards/<slug>, /status and /people; a person who is not the owner gets 403
// there and the section stays hidden. No token is ever shown here.
//
// Focus and announcements: the form and lists are drawn into one body element, the result of
// each save is said once in a status line outside it, and a redraw puts focus back on the same
// control by its data-reviews-* key.

export const FOCUS_KEYS = ["data-reviews-field", "data-reviews-save", "data-reviews-remove", "data-reviews-add", "data-reviews-retry", "data-reviews-login"];

export const READER_WORDS = {
  no_token: "Waiting for the owner's token. Nothing is read from GitHub until a workspace owner creates the comment reader's token for this workspace and gives the reader a read-only GitHub token on the machine where it runs.",
  waiting_first_read: "The comment reader has its token for this workspace but has not read a pull request yet. It starts once its read-only GitHub token is in place, and reads every few minutes after that.",
  reading: "The comment reader is working.",
  stale: "Comments may be missing: the comment reader has not read GitHub for more than 12 minutes. Check that it is still running on its machine.",
};

const ROLE_WORDS = { trusted: "Trusted reviewer", own: "The Coworker's own account" };
const sentence = (value) => value ? `${value[0].toUpperCase()}${value.slice(1)}${/[.!?]$/.test(value) ? "" : "."}` : "";
const LOGIN = /^[A-Za-z0-9_.[\]-]{1,100}$/;

export const refusalWords = (error) => {
  const text = String(error?.payload?.error || "").replace(/^no: /, "");
  if (text) return sentence(text);
  if (error?.status === 403) return "Only a workspace owner can change this.";
  return error?.status ? "That did not work on the server. Try again." : "That did not reach the server. Check the connection and try again.";
};

export function readerHTML(reader) {
  const state = READER_WORDS[reader?.state] ? reader.state : "no_token";
  const last = reader?.last_read_at ? ` Last read ${e(elapsed(reader.last_read_at) === "now" ? "just now" : `${elapsed(reader.last_read_at)} ago`)}.` : "";
  return `<p class="set-note" data-reviews-reader="${e(state)}">${e(READER_WORDS[state])}${state === "reading" || state === "stale" ? last : ""}</p>`;
}

export function settingsHTML({ board, reader, people }) {
  const rounds = [1, 2, 3].map((n) => `<option value="${n}"${Number(board?.rounds) === n ? " selected" : ""}>${n} ${n === 1 ? "round" : "rounds"}</option>`).join("");
  const form = `<form data-reviews-form><label class="set-check"><input type="checkbox" name="enabled" data-reviews-field="enabled"${board?.enabled ? " checked" : ""}> Let the Coworker answer review comments on this project's pull requests</label><p class="set-note">Each comment gets one answer from the card's Coworker: a fix in a new commit, a reply with its reason, or a question for you. It never merges, never changes the checks that judge the pull request, and never closes a reviewer's conversation. Off by default.</p><label>Rounds before it asks you<select name="rounds" data-reviews-field="rounds">${rounds}</select></label><p class="set-note">A round is one pass of the Coworker over the comments that wait. When the rounds are used up, the next comments wait for your answer on the card.</p><label class="set-check"><input type="checkbox" name="post_replies" data-reviews-field="post_replies"${board?.post_replies ? " checked" : ""}> Let the Coworker post its replies on public repositories</label><p class="set-note">Off: on a public repository the Coworker's replies are shown on the card first and not posted, so you can read them before anyone else does. Replies on private repositories are always posted.</p><button class="btn primary" type="submit" data-reviews-save="settings">Save</button></form>`;
  const list = (people || []).length
    ? `<ul class="set-list" data-reviews-people>${people.map((person) => `<li><span><b>${e(person.login)}</b> · ${e(ROLE_WORDS[person.role] || person.role)}</span><button class="btn sm" type="button" data-reviews-remove="${e(person.login)}" aria-label="Remove ${e(person.login)}">Remove</button></li>`).join("")}</ul>`
    : `<p class="set-note" data-reviews-people-empty>Nobody is listed. Owners, members and collaborators of the repository are trusted already.</p>`;
  const add = `<form data-reviews-add-form><label>GitHub account name<input name="login" data-reviews-login="login" autocomplete="off" spellcheck="false" maxlength="100" required></label><label>Counts as<select name="role" data-reviews-field="role"><option value="trusted">Trusted reviewer: the Coworker answers their comments</option><option value="own">The Coworker's own account: its comments are never answered</option></select></label><button class="btn" type="submit" data-reviews-add="person">Add</button></form>`;
  return `<h3>Comment reader</h3>${readerHTML(reader)}<h3>Answering comments</h3>${form}<h3>Who the Coworker listens to</h3><p class="set-note">Comments from the repository's owners, members and collaborators are answered. Add other people here, or the account the Coworker writes with, so it never answers itself.</p>${list}${add}`;
}

const offline = (error) => `<p class="set-note" data-reviews-offline>${e(error?.status ? "Could not load the review settings. Try again." : "Could not load the review settings. Check the connection and try again.")}</p><button class="btn" type="button" data-reviews-retry="load">Try again</button>`;

function focusedKey(scope, doc = globalThis.document) {
  const active = doc?.activeElement;
  if (!active || !scope?.contains?.(active)) return null;
  for (const name of FOCUS_KEYS) {
    if (active.hasAttribute?.(name)) return `[${name}="${String(active.getAttribute(name)).replace(/["\\]/g, "\\$&")}"]`;
  }
  return null;
}

// Fill the project's Review comments section (`section` holds `[data-project-reviews]`).
export async function mountReviewSettings(section, { project, api = request, current = () => true } = {}) {
  const host = section?.querySelector?.("[data-project-reviews]");
  if (!host || !project?.board) return;
  host.innerHTML = '<div data-reviews-body></div><p class="set-note" role="status" aria-live="polite" data-reviews-status></p>';
  const body = host.querySelector("[data-reviews-body]"), status = host.querySelector("[data-reviews-status]");
  const slug = encodeURIComponent(project.board);
  let wanted = null, last = null;
  const say = (note) => { status.textContent = note || ""; };
  const paint = (html) => {
    const had = focusedKey(host);
    body.innerHTML = html;
    section.hidden = false;
    const target = wanted || had;
    wanted = null;
    const node = target && host.querySelector(target);
    node?.focus?.({ preventScroll: true });
  };
  async function load(note) {
    try {
      const [board, state, people] = await Promise.all([
        api(`/api/pr-review/boards/${slug}`),
        api("/api/pr-review/status"),
        api("/api/pr-review/people"),
      ]);
      if (!current()) return;
      last = { board, reader: state?.reader, people: people?.people || [] };
      paint(settingsHTML(last));
      if (note !== undefined) say(note);
    } catch (error) {
      if (!current()) return;
      if ([403, 404].includes(error?.status)) { section.hidden = true; body.innerHTML = ""; return; }
      if (last) { paint(settingsHTML(last)); say(note ?? "Could not refresh the review settings. Showing what was loaded."); return; }
      paint(offline(error));
    }
  }
  async function act(trigger, path, method, payload, done, next) {
    const form = trigger.closest?.("form");
    const controls = [...(form?.querySelectorAll?.("button, input, select") || [trigger])];
    controls.forEach((control) => { control.disabled = true; });
    say("Saving…");
    let note;
    try {
      await api(path, { method, body: payload });
      note = done;
      wanted = next;
    } catch (error) {
      note = refusalWords(error);
      wanted = next;
      controls.forEach((control) => { control.disabled = false; });
    }
    await load(note);
  }
  host.addEventListener("submit", (event) => {
    const form = event.target.closest?.("form");
    if (!form) return;
    event.preventDefault();
    if (form.matches?.("[data-reviews-form]")) {
      const enabled = form.elements.enabled.checked;
      const payload = { enabled, rounds: Number(form.elements.rounds.value), post_replies: form.elements.post_replies.checked };
      const done = enabled
        ? `Saved. The Coworker answers review comments in this project, ${payload.rounds} ${payload.rounds === 1 ? "round" : "rounds"} before it asks you.`
        : "Saved. The Coworker does not answer review comments in this project.";
      void act(form, `/api/pr-review/boards/${slug}`, "PUT", payload, done, '[data-reviews-save="settings"]');
      return;
    }
    if (form.matches?.("[data-reviews-add-form]")) {
      const login = String(form.elements.login.value || "").trim();
      if (!LOGIN.test(login)) { say("Use the GitHub account name: letters, digits, dashes, dots or underscores."); return; }
      const role = form.elements.role.value;
      void act(form, "/api/pr-review/people", "PUT", { login, role }, `Added ${login} as ${ROLE_WORDS[role]?.toLowerCase() || role}.`, '[data-reviews-login="login"]');
    }
  });
  host.addEventListener("click", (event) => {
    const remove = event.target.closest?.("[data-reviews-remove]");
    if (remove) {
      const login = remove.dataset.reviewsRemove;
      void act(remove, "/api/pr-review/people", "PUT", { login, role: null }, `Removed ${login}.`, '[data-reviews-login="login"]');
      return;
    }
    if (event.target.closest?.("[data-reviews-retry]")) void load();
  });
  await load();
}

export const reviewSettingsSection = () => '<section class="set-sec" data-project-reviews-section hidden><h2>Review comments</h2><div data-project-reviews></div></section>';
