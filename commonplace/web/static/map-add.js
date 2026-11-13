/* The map's "Add machine" and "Add site": one POST each, then either "started" in words or a
   ready prompt copied to the clipboard. Logic only; the look is the screen's own. No form:
   the one thing asked is a domain name, for a site. */

export const ADD_PATHS = { machine: "/api/topology/add-machine", site: "/api/topology/add-site" };

const ASK = "Which domain should be added? For example example.com";

/** What happened, in words. `started` reuses the discovery lines; a prompt says what was copied. */
export function addLines(result, { copied = true } = {}) {
  const machines = Array.isArray(result?.machines) ? result.machines : [];
  if (result?.mode === "started") {
    const started = machines.filter((m) => m.outcome === "started" || m.outcome === "running");
    const where = started.map((m) => `${m.label || m.machine}${m.card ? ` (card #${m.card})` : ""}`).join(", ");
    const verb = started.every((m) => m.outcome === "running") ? "Already running on" : "Started on";
    return `${verb} ${where}. What it finds waits for your approval.`;
  }
  const what = result?.kind === "site" ? "trace the route" : "add this machine";
  const command = result?.command ? ` Or run this on the machine: ${result.command}` : "";
  return `${copied ? "No agent is available. The prompt is copied: paste it into an agent that can" : "No agent is available. The prompt is below: give it to an agent that can"} ${what}.${command}`;
}

/** Copies `text`; false when the browser refuses (the prompt is then shown instead). */
export async function copyText(text, clipboard = globalThis.navigator?.clipboard) {
  try {
    if (!clipboard?.writeText) return false;
    await clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** Presses one add button: `kind` is "machine" or "site". Returns the answer, or null when nothing was asked of the hub. */
export async function add(kind, { api, button, note, ask = (text) => globalThis.prompt?.(text), copy = copyText }) {
  const body = {};
  if (kind === "site") {
    const domain = (ask(ASK) || "").trim();
    if (!domain) return null;
    body.site = domain;
  }
  if (button) button.disabled = true;
  try {
    const result = await api(ADD_PATHS[kind], { method: "POST", body });
    if (result?.mode === "prompt") {
      const copied = await copy(result.prompt);
      if (note) note(`${addLines(result, { copied })}${copied ? "" : `\n\n${result.prompt}`}`);
    } else if (note) note(addLines(result));
    return result;
  } catch (failure) {
    if (note) note(`Could not add the ${kind}: ${failure?.message || "the request failed"}.`);
    return null;
  } finally {
    if (button) button.disabled = false;
  }
}

/** The handler a screen binds to its two buttons (`data-compute-action="add-machine"` and `"add-site"`). */
export function addAction({ api, note, ask, copy }) {
  return (action, button) => {
    const kind = action === "add-site" ? "site" : action === "add-machine" ? "machine" : null;
    return kind ? add(kind, { api, button, note, ask, copy }) : null;
  };
}
