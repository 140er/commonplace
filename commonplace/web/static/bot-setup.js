// What to do when a bot cannot answer yet: one plain sentence naming the setting and where it lives.
const SETUP = Object.freeze({
  needs_configuration: ["Bots need a chat model before they can answer.", "Choose one in Workspace settings. Next step: open a project's settings, go to Chat answers, then pick a local model or a hosted provider with its key.", "Ask the workspace owner to choose a chat model."],
  needs_turn_on: ["This project's chat model is not turned on yet.", "Turn it on in the project's settings, then send again.", "Ask the workspace owner to turn it on for this project."],
  key_missing: ["The chat model has no key yet.", "Add it in Workspace settings. Next step: open a project's settings, go to Chat answers, then add the provider key.", "Ask the workspace owner to add it."],
});

export function setupText(code, canConfigure) {
  const [what, owner, member] = typeof code === 'string' && Object.hasOwn(SETUP, code) ? SETUP[code] : SETUP.needs_configuration;
  return `${what} ${canConfigure ? owner : member}`;
}
