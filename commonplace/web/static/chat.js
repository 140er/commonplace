export const CHAT_COPY = Object.freeze({
  title: "Chat", loading: "Loading chat…", unavailable: "Chat is unavailable. Try again.",
  noProjects: "Choose a project to chat with its orchestrator.", empty: "Send a message to your project's orchestrator.",
  placeholder: "Message the orchestrator…", send: "Send", basic: "Basic replies (no model)",
  model: "Answers by {provider}/{model}", statusPrivate: "status private", decisions: "Decisions",
  noOrchestratorOwner: "No orchestrator for this project yet. Choose one above.",
  noOrchestratorMember: "No orchestrator for this project yet. Ask the workspace owner to choose one.",
});
export function chatHTML(page, me, e) { return ""; }
export function statusHTML(status, e) { return ""; }
export function createChat({mount, api, e}) { return { show(slug) {}, stop() {} }; }
