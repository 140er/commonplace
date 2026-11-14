// Deck bot settings. Names are display text; opaque references stay in request attributes.
import { compactSettingsFrame, compactSharingHTML } from './deck-coworker-sharing.js';
const enc = encodeURIComponent;
const button = (action, label, off = false, extra = "") => `<button type="button" class="bb-opt" data-bots-action="${action}"${off ? " disabled" : ""}${extra}>${label}</button>`;

export function permissionText({ talk, see_shared }, name = "This person") {
  if (talk && see_shared) return `${name} can talk privately, start shared conversations, and read and reply to shared conversations from when access is granted.`;
  if (talk) return `${name} can talk privately and start shared conversations. They can read and reply to shared conversations they start, but cannot read other shared conversations.`;
  if (see_shared) return `${name} can read shared conversations from when access is granted. They cannot send messages or start conversations.`;
  return `${name} has no access to this bot.`;
}

export function permissionSummary(permissions) {
  return permissionText(permissions, "").trim().replace(/^can/, "Can").replace(/^has/, "Has");
}

export function accessLogTime(at, now = new Date()) {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return at;
  const day = value => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  const time = date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
  if (day(date) === day(now)) return `Today ${time}`;
  if (day(date) === day(yesterday)) return `Yesterday ${time}`;
  return `${date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}, ${time}`;
}

export function settingsFrameHTML(sheet, bot, content, e) {
  const tabs = [["people", bot.mine || bot.lifecycle ? "People & permissions" : "Your access"], ["log", "Access log"]];
  if (bot.mine || bot.lifecycle) tabs.push(["details", "Name, job & memory"], ["learning", "Learning & sharing"]);
  const nav = `<nav class="bot-access-tabs" data-cb-scroll aria-label="Bot settings">${tabs.map(([key, label]) => button("sharing-tab", label, sheet.busy || (["details", "learning"].includes(key) && (!bot.mine || bot.frozen)), ` data-tab="${key}" aria-pressed="${(sheet.tab || "people") === key}"`)).join("")}</nav>`;
  if (sheet.capy) return compactSettingsFrame(sheet, bot, content, nav, e);
  return `<div class="sheet bot-settings-sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-h"><div class="sheet-body bot-access"><header class="bot-access-heading"><div><h2 id="sheet-h">${sheet.capy ? `Who can use ${e(bot.name)}` : 'Bot settings'}</h2><p>${e(bot.name)}</p></div>${button("close", "Close")}</header>${nav}${content}</div></div>`;
}

export function audienceText(bot, shares, { mine = false } = {}) {
  const owner = bot.owner?.name || "the bot’s owner";
  if (!mine) return `${owner}, you, and everyone allowed to see this bot’s shared conversations can read this conversation. People granted access later can read only new messages.`;
  const names = shares.filter(row => row.see_shared).map(row => row.user.name);
  return `${[owner, ...names].join(", ")} can read this conversation. Its creator can also read and reply. People granted access later can read only new messages.`;
}

export function accessLogText(row, people = {}) {
  const name = id => people[`user:${id}`] || "a former workspace member";
  const actor = name(row.actor_id), member = name(row.member_id);
  switch (row.action) {
    case "share": return `${actor} gave ${member} access to this bot.`;
    case "unshare": return `${actor} removed ${member}’s access to this bot.`;
    case "permissions": return `${actor} changed ${member}’s permissions for this bot.`;
    case "adopt": return `${actor} became the bot’s owner after ${member} left.`;
    case "freeze": return row.mode_after === "lifted" ? `${actor} enabled the bot again.` : row.ref === "owner_left" ? `${actor} froze the bot because its owner left the workspace.` : `${actor} disabled the bot for everyone.`;
    case "remove": return `${actor} removed the bot’s access to ${row.ref || "a resource"}.`;
    case "grant": case "change": return `${actor} ${row.action === "grant" ? "allowed" : "changed permission for"} the bot to ${row.mode_after === "write" ? "read and write" : row.mode_after === "read" ? "read" : "use"} ${row.ref || "a resource"}.`;
    default: return `${actor} updated access to this bot.`;
  }
}

export function sharingHTML(sheet, bot, e) {
  if (sheet.capy && bot.mine && !bot.frozen && !bot.lifecycle && sheet.settings?.sharing === true) return settingsFrameHTML(sheet, bot, compactSharingHTML(sheet, bot, e, { accessLogText, accessLogTime, permissionText }), e);
  const off = sheet.busy || sheet.loading, owner = bot.mine === true;
  const frozen = bot.frozen === true;
  const editable = owner && !frozen && bot.kind === "custom" && sheet.settings?.sharing === true;
  const rows = sheet.shares || [], selected = sheet.selected;
  const activeRows = sheet.settings?.sharing ? rows : [];
  const canManage = sheet.me?.can_admin === true;
  const action = (key, text, extra = "") => button(key, text, off, extra);
  let content = "";
  if (frozen) content += `<section class="bot-access-card" data-frozen><h3>${bot.frozen_reason === "owner_left" ? "This bot needs a new owner" : "This bot is disabled"}</h3><p>${bot.frozen_reason === "owner_left" ? "Its owner left the workspace. Conversations and access are kept, but nobody can talk to it. A workspace owner can adopt it. The former owner’s private conversation stays private." : "Nobody can talk to this bot until a workspace owner enables it again."}</p>${canManage ? `<div class="sheet-btns"><button type="button" class="bb-opt go" data-bots-action="${bot.frozen_reason === "owner_left" ? "adopt" : "enable"}"${off ? " disabled" : ""}>${bot.frozen_reason === "owner_left" ? "Adopt bot" : "Enable bot"}</button></div>` : ""}</section>`;
  if (sheet.loading) content += '<p role="status">Loading bot settings…</p>';
  else if (sheet.tab === "log") {
    content += `<section class="bot-access-card" data-access-log><h3>Access log</h3>${frozen ? '<p>The log is available after the bot is enabled or adopted.</p>' : (sheet.log || []).length ? `<ol>${sheet.log.map(row => `<li><p>${e(accessLogText(row, sheet.me?.people))}</p><time datetime="${e(row.at)}" title="${e(row.at)}">${e(accessLogTime(row.at))}</time></li>`).join("")}</ol>${sheet.moreLog ? action("sharing-more", "More changes") : ""}` : '<p>No access changes yet.</p>'}</section>`;
  } else if (!owner && !bot.lifecycle) {
    content += `<section class="bot-access-card" data-member-access><h3>Your access</h3><p>Shared by ${e(bot.owner?.name || "the bot’s owner")}.</p><p>${e(permissionText(bot.perms || {}, "You").replace("They cannot", "You cannot").replace("They can", "You can").replace("they start", "you start"))}</p><p>Your private conversation is visible only to you. The bot’s owner and workspace owners cannot read it.</p><p>Only the bot’s owner can change your access.</p></section>`;
  } else if (!frozen) {
    content += `<section class="bot-access-card"><h3>Who can talk to this bot</h3><p>${e([bot.owner?.name || "You", ...activeRows.filter(r => r.talk).map(r => r.user.name)].join(", "))}.</p><h3>Who can see shared conversations</h3><p>${e([bot.owner?.name || "You", ...activeRows.filter(r => r.see_shared).map(r => r.user.name)].join(", "))}. A conversation’s creator can also read and reply.</p><p>Private conversations stay visible only to the person who starts them. Sharing never opens an existing private conversation.</p></section>`;
    if (bot.kind !== "custom" && !bot.lifecycle) content += '<p>Personal and project bots stay private. Create a custom bot to share it.</p>';
    else if (!frozen) {
      if (sheet.settings?.sharing === false) content += `<section class="bot-access-card"><h3>Bot sharing is off</h3><p>${canManage ? "Turn on bot sharing for this workspace before granting access." : "Ask a workspace owner to turn on bot sharing."}</p>${canManage ? action("sharing-enable", "Turn on bot sharing") : ""}</section>`;
      content += `<section class="bot-access-card" data-people-access><div class="bot-access-heading"><div><h3>People with access</h3>${sheet.saved ? '<p role="status" data-access-saved>Access saved.</p>' : ""}</div>${editable ? action("sharing-picker", "Add a person") : ""}</div><ul class="bot-access-people">${rows.map(row => `<li><div><b>${e(row.user.name)}</b><p>${e(permissionSummary(row))}</p></div>${editable ? action("sharing-edit", "Change access", ` data-user="${e(row.user.id)}"`) : ""}</li>`).join("") || '<li>No one else has access.</li>'}</ul></section>`;
    }
    if (sheet.picker && editable) {
      const people = Object.entries(sheet.me?.people || {}).filter(([ref]) => ref.startsWith("user:") && ref !== `user:${bot.owner?.id}` && !rows.some(r => ref === `user:${r.user.id}`));
      content = `<section class="bot-access-card" data-people-picker><h3>Choose a workspace person</h3><label>Find a person by name<input type="search" name="person-search" value="${e(sheet.search || "")}" autocomplete="off" data-focus></label><ul class="bot-access-people">${people.map(([ref, name]) => `<li data-person-name="${e(name.toLocaleLowerCase())}"${String(name).toLocaleLowerCase().includes((sheet.search || "").toLocaleLowerCase()) ? "" : " hidden"}><span>${e(name)}</span>${action("sharing-select", "Choose", ` data-user="${e(ref.slice(5))}"`)}</li>`).join("")}</ul><p data-picker-empty${people.some(([, name]) => String(name).toLocaleLowerCase().includes((sheet.search || "").toLocaleLowerCase())) ? " hidden" : ""}>No matching workspace people.</p></section>`;
    }
    if (selected && editable) {
      const draft = sheet.draft || {}, name = sheet.me.people[`user:${selected}`];
      const existing = rows.some(row => row.user.id === selected);
      content = `<section class="bot-access-card" data-permission-editor><h3>Access for ${e(name)}</h3><form data-bots-form="share"><label class="bot-access-check"><input type="checkbox" name="talk"${draft.talk ? " checked" : ""}>Can talk to this bot</label><label class="bot-access-check"><input type="checkbox" name="see_shared"${draft.see_shared ? " checked" : ""}>Can see shared conversations</label><div class="bot-access-preview" aria-live="polite"><h4>Before you save</h4><p>${e(permissionText(draft, name))}</p><p>Private conversations stay private. Newly added people see only new shared messages.</p></div><div class="sheet-btns"><button type="submit" class="bb-opt go"${off || !draft.talk && !draft.see_shared ? " disabled" : ""}>${existing ? "Save access" : "Grant access"}</button>${existing ? action("sharing-revoke", sheet.confirmRevoke ? "Remove access now?" : "Revoke access") : ""}${action("sharing-cancel", "Cancel")}</div></form></section>`;
    }
    if (sheet.access?.entries?.length) content += `<section class="bot-access-card"><h3>What this bot may use</h3><ul>${sheet.access.entries.map(entry => `<li>${e(entry.ref)} · ${entry.mode === "write" ? "read and write" : entry.mode === "read" ? "read only" : "use"}</li>`).join("")}</ul><p>Each person’s own permissions still apply.</p></section>`;
  }
  return settingsFrameHTML(sheet, bot, `${sheet.error ? `<p role="alert">${e(sheet.error)}</p>${action("sharing-retry", "Retry")}` : ""}${content}`, e);
}

export function threadsHTML(bot, threads, selected, e) {
  if (!bot.shared || bot.frozen) return "";
  const privateAllowed = bot.mine || bot.perms?.talk;
  const shared = threads.filter(t => t.visibility === "shared" && !t.archived);
  return `<div class="bot-thread-audience"><label>Conversation<select name="bot-thread">${privateAllowed ? `<option value=""${!selected ? " selected" : ""}>Private · only you</option>` : ""}${shared.map(t => `<option value="${e(t.id)}"${selected === t.id ? " selected" : ""}>Shared · ${e(t.title || "Untitled conversation")}</option>`).join("")}</select></label>${privateAllowed ? button("shared-thread", "New shared conversation") : ""}<p>${selected ? "Shared conversation · visible to the bot’s owner, its creator, and people allowed to see shared conversations." : privateAllowed ? "Private conversation · only you can read it, including past and future messages." : "You can read shared conversations. No shared conversations yet."}</p></div>`;
}

export function threadPreviewHTML(sheet, bot, e) {
  return `<div class="sheet bot-settings-sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-h"><div class="sheet-body bot-access"><h2 id="sheet-h">New shared conversation</h2>${sheet.loading ? '<p role="status">Loading audience…</p>' : `<section class="bot-access-card"><h3>Who can see this conversation</h3><p>${e(audienceText(bot, sheet.shares || [], { mine: bot.mine }))}</p><p>This starts a new conversation. Your private messages are never copied here.</p></section><form data-bots-form="shared-thread"><label>Conversation name<input name="title" maxlength="200" autocomplete="off"></label><div class="sheet-btns"><button type="submit" class="bb-opt go"${sheet.busy || sheet.error ? " disabled" : ""}>Start shared conversation</button>${button("close", "Cancel")}</div></form>`}${sheet.error ? `<p role="alert">${e(sheet.error)}</p>` : ""}</div></div>`;
}
