/* The map's "Start agent work on map" button: one POST to /api/topology/discovery, then what
   happened on every machine, in words. Logic only; the look is the map's own (`.topo-off`). */

export const DISCOVERY_PATH = "/api/topology/discovery";

const WHY = {
  never_reported: "has never reported to the hub",
  no_heartbeat: "has not been heard from for a while",
  disabled: "is turned off",
  not_attested: "is not attested yet",
  no_topology_wing: "has no access to the topology wing",
  no_project_or_topic: "has no project or topic to run with",
};

/** One line per machine: started, already running, not reachable or not started, with the reason. */
export function discoveryLines(result) {
  const machines = Array.isArray(result?.machines) ? result.machines : [];
  if (!machines.length) return ["No runner machines yet. Add a machine first, then start again."];
  return machines.map((m) => {
    const who = m.label || m.machine;
    const card = m.card ? ` (card #${m.card})` : "";
    const why = WHY[m.reason] || m.reason || "";
    if (m.outcome === "started") return `${who}: started${card}.`;
    if (m.outcome === "running") return `${who}: already running${card}.`;
    if (m.outcome === "unreachable") return `${who}: not reachable, it ${why || "did not answer"}.`;
    return `${who}: not started${why ? `, it ${why}` : ""}.`;
  });
}

/** Presses the button once: off while the request runs, the outcome in `note`, the button back on. */
export async function startDiscovery({ api, button, note }) {
  if (button) button.disabled = true;
  try {
    const result = await api(DISCOVERY_PATH, { method: "POST", body: {} });
    if (note) note.textContent = discoveryLines(result).join(" ");
    return result;
  } catch (failure) {
    if (note) note.textContent = `Could not start agent work: ${failure?.message || "the request failed"}.`;
    return null;
  } finally {
    if (button) button.disabled = false;
  }
}

/** The handler a screen gives the map as `onStartWork`: finds the button in the mounted map. */
export function discoveryAction({ api, root }) {
  return () => {
    const host = typeof root === "function" ? root() : root;
    const button = host?.querySelector?.('[data-topo="start"]') || null;
    let note = host?.querySelector?.('[data-topo="discovery-note"]') || null;
    if (!note && button?.insertAdjacentHTML) {
      button.insertAdjacentHTML("afterend", '<p class="topo-off" role="status" data-topo="discovery-note"></p>');
      note = host.querySelector('[data-topo="discovery-note"]');
    }
    return startDiscovery({ api, button, note });
  };
}
