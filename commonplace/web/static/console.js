// Frozen console interface; implemented by U6.
export const CONSOLE_COPY = Object.freeze({
  "loading": "Loading agents…",
  "noAgentsOwner": "No agents connected. Create one in Settings → Agents.",
  "noAgentsMember": "No agents act as you in this workspace.",
  "showTools": "Show tool calls",
  "stopTools": "Stop showing tool calls",
  "toolsOff": "Tool calls are off for this agent. Only you can see them when on.",
  "toolsPrivate": "Tool calls and output are shown only to the person this agent acts for.",
  "waiting": "Waiting for activity. Tool calls appear as they happen.",
  "noBoard": "No board or memory actions yet.",
  "outputHeading": "Output",
  "activityHeading": "Activity",
  "noOutput": "No replies or comments yet.",
  "unavailable": "Console unavailable. Retrying…"
});

export function markRedactions(escapedHtml) {
  throw new Error("Not implemented");
}

export function agentListHTML(agents, selectedId, e) {
  throw new Error("Not implemented");
}

export function streamItemsHTML(items, e) {
  throw new Error("Not implemented");
}

export function outputItemsHTML(items, e) {
  throw new Error("Not implemented");
}

export function consoleView({ role, agents, selected, items, output, tools, error, loading }, e) {
  throw new Error("Not implemented");
}

export function createConsole({ mount, api, e, setTimer = setTimeout, clearTimer = clearTimeout, hidden = () => document.hidden }) {
  throw new Error("Not implemented");
}
