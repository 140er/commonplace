export const PROJECT_COPY = Object.freeze({
  connected: "connected", disconnected: "not connected", waiting: "waiting for the agent",
  statusPrivate: "status private", connect: "Connect agent", settings: "Project settings",
  sharedExplain: "This project keeps its memory in the shared section that every project uses. An agent connected here could read and change all of it.",
  ownWing: "Give this project its own memory section",
  allowShared: "Allow access to all shared memory",
  ownWingEffect: "Agents that can only read shared memory will stop seeing this project. Its coordinator may be cleared. This can't be undone here.",
  ownWingDone: "This project now has its own memory section.",
  wingTaken: "Another project already uses this memory section name. Nothing was changed.",
  wingInUse: "Some agent connections would gain access to this project. Nothing was changed.",
  sharedWing: "This project uses the shared memory wing. Connecting gives the agent read and write access to all shared memory.",
  loadError: "Could not load project settings.", connectError: "Could not connect the agent.",
  saveError: "Could not save project models.", saved: "Project models saved.",
  copyError: "Copy failed. Select the token and copy it manually.",
});

export function sharedWingHTML(payload, e) {
  return `<section class="form-tile"><h2 id="project-wing-title">Project memory</h2><p>${e(payload.explanation || PROJECT_COPY.sharedExplain)}</p><p>${e(payload.fix?.effect || PROJECT_COPY.ownWingEffect)}</p><button type="button" class="blockbtn" data-wing-choice="own">${e(PROJECT_COPY.ownWing)}</button><button type="button" class="opt" data-wing-choice="shared">${e(PROJECT_COPY.allowShared)}</button><button type="button" class="opt" data-wing-choice="cancel">Cancel</button></section>`;
}

export function memoryHTML(project, e) {
  const memory = project.memory;
  if (!memory) return "";
  const label = memory.shared ? "shared section" : memory.wing.startsWith("project_") && !/[*?\[\]]/.test(memory.wing) ? "own section" : memory.wing;
  return `<div class="setting-row"><span>Memory: ${e(label)}</span>${memory.shared && project.can_configure ? `<button type="button" class="opt" data-project-own-wing>${e(PROJECT_COPY.ownWing)}</button>` : ""}</div>`;
}

function chooseSharedWing(payload, e) {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.setAttribute("aria-labelledby", "project-wing-title");
    dialog.innerHTML = sharedWingHTML(payload, e);
    let settled = false;
    const dismiss = (choice = "cancel") => {
      if (settled) return;
      settled = true;
      dialog.remove();
      window.removeEventListener("hashchange", cancel);
      window.removeEventListener("pagehide", cancel);
      document.removeEventListener("session-expired", cancel);
      resolve(choice);
    };
    const cancel = () => dismiss();
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); cancel(); });
    dialog.addEventListener("close", cancel);
    dialog.querySelectorAll("[data-wing-choice]").forEach(button => {
      button.onclick = () => dismiss(button.dataset.wingChoice);
    });
    window.addEventListener("hashchange", cancel);
    window.addEventListener("pagehide", cancel);
    document.addEventListener("session-expired", cancel);
    document.body.append(dialog);
    dialog.showModal();
  });
}

export function mainHTML(project, e) {
  const main = project.main;
  const status = main.connected ? (main.seen ? main.status || PROJECT_COPY.statusPrivate : PROJECT_COPY.waiting) : "";
  return `<div class="setting-row" data-project-main><span>${e(main.name)} · ${e(main.connected ? PROJECT_COPY.connected : PROJECT_COPY.disconnected)}${status ? ` · ${e(status)}` : ""}</span>${project.can_configure && main.connectable ? `<button type="button" class="opt" data-project-connect>${e(PROJECT_COPY.connect)}</button>` : ""}</div>`;
}

export function connectResultHTML(result, e) {
  return `<section class="form-tile"><h2 id="project-connect-title">Agent connection</h2><p>Connect attaches an agent to this project's automatic orchestrator seat.</p><p>Copy now. This token will not be shown again.</p><label>Token<input data-project-token value="${e(result.token)}" readonly autocomplete="off" spellcheck="false"></label><button type="button" class="opt" data-project-copy>Copy token</button><label>MCP URL<input value="${e(result.mcp_url)}" readonly></label><p>Use this token as a Bearer token in your agent's MCP configuration. Then call webhook_add to receive project chat messages:</p><pre>${e(JSON.stringify(result.setup?.webhook_add || {}, null, 2))}</pre>${result.warning ? `<p role="status">${e(result.warning)}</p>` : ""}<p role="status" data-project-copy-status></p><button type="button" class="blockbtn" data-project-dismiss>Close</button></section>`;
}

export function modelsHTML(project, e) {
  return `<form data-project-models><p>Leave both fields empty to use the runner's default for that role.</p>${["main", "worker"].map((role) => {
    const choice = project.models?.[role];
    return `<fieldset ${project.can_configure ? "" : "disabled"}><legend>${role === "main" ? "Main orchestrator" : "Worker"}</legend><label>Provider<input name="${role}_provider" value="${e(choice?.provider)}" maxlength="128" autocomplete="off"></label><label>Model<input name="${role}_model" value="${e(choice?.model)}" maxlength="120" autocomplete="off"></label></fieldset>`;
  }).join("")}${project.can_configure ? '<button class="blockbtn" type="submit">Save models</button>' : ""}<p role="status" data-project-model-status></p></form>`;
}

function showConnectResult(result, e) {
  const dialog = document.createElement("dialog");
  dialog.setAttribute("aria-labelledby", "project-connect-title");
  dialog.innerHTML = connectResultHTML(result, e);
  const dismiss = () => {
    dialog.replaceChildren();
    dialog.remove();
    window.removeEventListener("hashchange", dismiss);
    window.removeEventListener("pagehide", dismiss);
    document.removeEventListener("session-expired", dismiss);
  };
  dialog.addEventListener("close", dismiss);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); dismiss(); });
  dialog.querySelector("[data-project-dismiss]").onclick = dismiss;
  dialog.querySelector("[data-project-copy]").onclick = async () => {
    try {
      await navigator.clipboard.writeText(dialog.querySelector("[data-project-token]").value);
      if (dialog.isConnected) dialog.querySelector("[data-project-copy-status]").textContent = "Copied";
    } catch {
      if (dialog.isConnected) dialog.querySelector("[data-project-copy-status]").textContent = PROJECT_COPY.copyError;
    }
  };
  window.addEventListener("hashchange", dismiss);
  window.addEventListener("pagehide", dismiss);
  document.addEventListener("session-expired", dismiss);
  document.body.append(dialog);
  dialog.showModal();
  dialog.querySelector("[data-project-token]").focus();
}

// Both app.js entry points keep their handlers here.
export async function mountProject(host, { slug, api, e, settings = false, current = () => true, notice = "" }) {
  const path = `/api/projects/${encodeURIComponent(slug)}`;
  const active = () => current() && host.isConnected && !host.closest("[hidden]");
  host.innerHTML = '<p role="status">Loading project…</p>';
  let project, connecting = false;
  try { project = await api(path); }
  catch {
    if (active()) host.innerHTML = `<p role="status">${e(PROJECT_COPY.loadError)}</p>`;
    return;
  }
  if (!active()) return;

  function renderMain() {
    host.querySelector("[data-project-heading]").innerHTML = mainHTML(project, e);
    const hint = host.querySelector("[data-project-notice]");
    if (hint) { hint.hidden = project.main.connected; hint.textContent = notice; }
    const button = host.querySelector("[data-project-connect]");
    if (button) { button.disabled = connecting; button.onclick = connect; }
    const memory = host.querySelector("[data-project-memory]");
    if (memory) {
      memory.innerHTML = memoryHTML(project, e);
      const own = memory.querySelector("[data-project-own-wing]");
      if (own) { own.disabled = connecting; own.onclick = configureWing; }
    }
  }
  function wingError(error) {
    return error.payload?.error === "wing_taken" ? PROJECT_COPY.wingTaken : error.payload?.error === "wing_in_use" ? PROJECT_COPY.wingInUse : PROJECT_COPY.connectError;
  }
  async function configureWing() {
    if (connecting) return;
    const choice = await chooseSharedWing({}, e);
    if (choice !== "own" || !active()) return;
    connecting = true; renderMain();
    const status = host.querySelector("[data-project-connect-status]");
    try {
      const result = await api(`${path}/wing`, { method: "POST", body: {} });
      if (!active()) return;
      project = result;
      status.textContent = result.changed ? PROJECT_COPY.ownWingDone : "";
    } catch (error) { if (active()) status.textContent = wingError(error); }
    finally { connecting = false; if (active()) renderMain(); }
  }
  async function connect(event) {
    const button = event.currentTarget;
    if (connecting || button.disabled) return;
    const body = {};
    if (project.main.connected) {
      const adopted = project.main.source === "coordinator";
      if (!confirm(adopted ? `Connect replaces ${project.main.name} as this project's orchestrator. Continue?` : "Reconnect this agent? Its current token will stop working.")) return;
      if (adopted) body.replace = true;
    }
    connecting = true; button.disabled = true;
    const status = host.querySelector("[data-project-connect-status]");
    status.textContent = "";
    try {
      let result, wingChanged = false;
      for (;;) {
        try { result = await api(`${path}/orchestrator/connect`, { method: "POST", body }); break; }
        catch (error) {
          if (!active()) return;
          if (error.status === 409 && error.payload?.error === "shared_wing") {
            const choice = await chooseSharedWing(error.payload, e);
            if (choice === "cancel" || !active()) return;
            if (choice === "shared") body.allow_shared = true;
            else {
              const owned = await api(error.payload.fix.path, { method: "POST", body: {} });
              if (!active()) return;
              project = owned;
              wingChanged = owned.changed;
              if (wingChanged) status.textContent = PROJECT_COPY.ownWingDone;
              renderMain();
            }
          } else if (error.status === 409 && error.payload?.error === "replaces current orchestrator" && !body.replace) {
            if (!confirm(`${wingChanged ? PROJECT_COPY.ownWingDone + " " : ""}Connect replaces the current orchestrator. Continue?`)) return;
            body.replace = true;
          } else throw error;
        }
      }
      if (!active()) return;
      showConnectResult(result, e);
      result = null;
      project = await api(path);
      if (active()) renderMain();
    } catch (error) {
      if (active()) status.textContent = wingError(error);
    } finally {
      connecting = false; button.disabled = false;
      const next = host.querySelector("[data-project-connect]");
      if (next) next.disabled = false;
    }
  }
  host.innerHTML = `${settings ? `<h2>${e(PROJECT_COPY.settings)}</h2>` : ""}<div data-project-heading></div><p data-project-notice hidden></p><p role="status" data-project-connect-status></p>${settings ? `<div data-project-memory></div>${modelsHTML(project, e)}` : ""}`;
  renderMain();
  const form = host.querySelector("[data-project-models]");
  if (form && project.can_configure) form.onsubmit = async (event) => {
    event.preventDefault();
    event.stopPropagation();
    const button = form.querySelector('button[type="submit"]');
    if (button.disabled) return;
    const status = form.querySelector("[data-project-model-status]"), body = {};
    for (const role of ["main", "worker"]) {
      const provider = form.elements.namedItem(`${role}_provider`).value.trim();
      const model = form.elements.namedItem(`${role}_model`).value.trim();
      if (!!provider !== !!model) { status.textContent = "Enter both provider and model, or leave both empty."; return; }
      body[role] = provider ? { provider, model } : null;
    }
    button.disabled = true;
    status.textContent = "";
    try {
      const saved = await api(`${path}/models`, { method: "PUT", body });
      if (!active()) return;
      project = saved;
      status.textContent = PROJECT_COPY.saved;
      renderMain();
    } catch {
      if (active()) status.textContent = PROJECT_COPY.saveError;
    } finally { button.disabled = false; }
  };
}
