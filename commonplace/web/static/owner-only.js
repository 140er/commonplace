import { escape as e } from "./api.js";

// One plain line where an owner-only control would sit, so a member is told why it is missing and who
// to ask. UI copy only: the server still refuses the write, and the line names no data the member cannot read.
export const OWNER_ONLY = "Only the workspace owner can change this.";
export const ASK_OWNER = "Ask the workspace owner.";

export const OWNER_ONLY_COPY = {
  connections: "Only the workspace owner can add or change connected apps. To connect an app of your own, ask the workspace owner to create a connection for you.",
  project: "Only the workspace owner can change this project's settings. You can read them here. Ask the workspace owner for a change.",
  models: "Only the workspace owner can change the models. Ask the workspace owner for a change.",
  orchestration: "Only the workspace owner can turn this on or off. Ask the workspace owner for a change.",
  release: "Only the workspace owner, or this project's coordinator, can release this card.",
  priority: "Only the workspace owner can change the priority or task type.",
};

export const ownerOnlyNote = (text = OWNER_ONLY) => `<p class="set-note" data-owner-only>${e(text)}</p>`;
export const ownerOnlyHint = (text = OWNER_ONLY) => `<small class="set-note" data-owner-only>${e(text)}</small>`;

// A settings section the server refused: for a member, the reason replaces the raw error. Owners keep the error.
export function ownerOnlySectionHTML(name, error, viewer, copy = OWNER_ONLY_COPY.connections) {
  if (error?.status !== 403 || viewer?.can_admin === true) return null;
  return `<section class="settings-section" data-owner-only-section><h2>${e(name)}</h2>${ownerOnlyNote(copy)}</section>`;
}
