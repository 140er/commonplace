/* Workbench keyboard behaviour for the static screens (design reference, no network).
   Demonstrates: initial focus, roving tabindex on the board, tablists, the palette
   listbox, modal focus trap + Esc + focus return, and the single-key shortcut rules. */
let showDetailsRef = null;
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const typing = (el) => el && (el.matches("input, textarea, select, [contenteditable]") || el.isContentEditable);

  // 1. Initial focus: autofocus fields, or an element marked data-autofocus (headings, Cancel).
  addEventListener("DOMContentLoaded", () => {
    const first = $("[autofocus]") || $("[data-autofocus]");
    if (first) first.focus();
  });
  // The painted .is-focus ring only documents the first state in the screenshots; real focus styling takes over.
  document.addEventListener("focusin", (e) => {
    $$(".is-focus").forEach((el) => { if (el !== e.target) el.classList.remove("is-focus"); });
  });

  // 2. Board: one tab stop per column, arrows move inside and across columns.
  const ITEM = ".card, .done-link";
  const cols = () => $$(".board .col").map((c) => $$(ITEM, c)).filter((c) => c.length);
  const moveTo = (card) => {
    const col = card.closest(".col");
    $$(ITEM, col).forEach((c) => (c.tabIndex = c === card ? 0 : -1));
    card.focus();
  };
  document.addEventListener("keydown", (e) => {
    const card = e.target.closest && e.target.closest(".board .card, .board .done-link");
    if (!card || e.target !== card) return;
    const all = cols();
    const ci = all.findIndex((c) => c.includes(card));
    const i = all[ci].indexOf(card);
    let next = null;
    if (e.key === "ArrowDown") next = all[ci][i + 1];
    if (e.key === "ArrowUp") next = all[ci][i - 1];
    if (e.key === "Home") next = all[ci][0];
    if (e.key === "End") next = all[ci][all[ci].length - 1];
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      const target = all[ci + (e.key === "ArrowRight" ? 1 : -1)];
      if (target) next = target.find((c) => c.tabIndex === 0) || target[0];
    }
    if (e.key === "Enter" && card.matches(".card")) {
      $$(".board .card").forEach((c) => { c.classList.remove("is-selected"); c.removeAttribute("aria-current"); });
      card.classList.add("is-selected");
      card.setAttribute("aria-current", "true");
      if (typeof showDetailsRef === "function") showDetailsRef($(".cid", card).textContent, $("#chat") && !$("#chat").hidden ? "board" : null);
    }
    if (next) { e.preventDefault(); moveTo(next); }
  });

  // 3. Tablists: arrows move selection and focus (context panel tabs, mobile column tabs).
  document.addEventListener("keydown", (e) => {
    const tab = e.target.closest && e.target.closest('[role="tab"]');
    if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const tabs = $$('[role="tab"]', tab.closest('[role="tablist"]'));
    let i = tabs.indexOf(tab);
    i = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : (i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    tabs.forEach((t, j) => { t.setAttribute("aria-selected", String(j === i)); t.tabIndex = j === i ? 0 : -1; });
    tabs[i].focus();
    e.preventDefault();
  });

  // 4. Palette: arrows move the active option; disabled options are skipped.
  document.addEventListener("keydown", (e) => {
    const input = e.target.closest && e.target.closest('input[role="combobox"]');
    if (!input || !["ArrowDown", "ArrowUp"].includes(e.key)) return;
    const list = document.getElementById(input.getAttribute("aria-controls"));
    const opts = $$('[role="option"]:not([aria-disabled="true"])', list);
    let i = opts.findIndex((o) => o.getAttribute("aria-selected") === "true");
    i = (i + (e.key === "ArrowDown" ? 1 : -1) + opts.length) % opts.length;
    opts.forEach((o, j) => o.setAttribute("aria-selected", String(j === i)));
    if (!opts[i].id) opts[i].id = "opt-" + i;
    input.setAttribute("aria-activedescendant", opts[i].id);
    opts[i].scrollIntoView({ block: "nearest" });
    e.preventDefault();
  });

  // 5. Modals: Tab stays inside, Esc closes, focus returns to the trigger, background un-inerts.
  const modal = () => $('[aria-modal="true"]:not([hidden])');
  const focusables = (root) => $$('a[href], button:not([disabled]), input, textarea, select, [tabindex="0"]', root).filter((el) => !el.closest("[hidden]"));
  document.addEventListener("keydown", (e) => {
    const m = modal();
    if (!m) return;
    if (e.key === "Tab") {
      const f = focusables(m);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    }
    if (e.key === "Escape" && $('[data-esc="follow"]', m)) { e.preventDefault(); location.href = $('[data-esc="follow"]', m).href; return; }
    if (e.key === "Escape") {
      if (m.id === "chat" || (m.id === "ctx" && $(".shell.ctx-pushed"))) return;
      e.preventDefault();
      m.hidden = true;
      $$(".scrim, .tablet-scrim").forEach((s) => (s.hidden = true));
      $$(".panel-open").forEach((el) => el.classList.remove("panel-open"));
      $$("[aria-expanded='true'][aria-controls='" + m.id + "']").forEach((b) => b.setAttribute("aria-expanded", "false"));
      $$("[inert]").forEach((el) => el.removeAttribute("inert"));
      const back = document.getElementById(m.dataset.returnFocus || "");
      if (back) back.focus();
    }
  });

  // 6. Single-key shortcuts: never while typing, never with modifiers, and Approve/Decline have none.
  const keys = { c: "#add-card", r: "#ask-question" };
  document.addEventListener("keydown", (e) => {
    if (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); $(".cmdk") && $(".cmdk").click(); return; }
    if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || modal()) return;
    if (localStorage.getItem("wb-single-keys") === "off") return;
    if (e.key === "/") { e.preventDefault(); openChat($("#chat-toggle")); return; }
    if (e.key === "]" && chatOpen()) { e.preventDefault(); closeChat(); return; }
    if (e.key === "]" && $("#from-chat") && !$("#from-chat").hidden) { e.preventDefault(); backToChat(); return; }
    const target = keys[e.key.toLowerCase()] && $(keys[e.key.toLowerCase()]);
    if (target) { e.preventDefault(); if (e.key.toLowerCase() === "r") target.click(); else target.focus(); }
  });

  // 8. Chat (B + chat): one toggle opens and closes the project chat in the right-hand slot.
  //    Opening hides the context panel, moves focus into the message box and sets aria-expanded on every opener;
  //    closing brings the context panel back and returns focus to the control that opened it.
  const pane = () => $("#chat");
  const chatOpen = () => pane() && !pane().hidden;
  let opener = null;
  // Below 1024 px the chat (and a card opened from it) is a modal sheet: role=dialog, aria-modal, rail and board inert.
  const narrow = () => innerWidth < 1024;
  function sheet(el, on) {
    if (!el) return;
    const modalNow = on && narrow();
    if (modalNow) { el.setAttribute("role", "dialog"); el.setAttribute("aria-modal", "true"); }
    else { el.removeAttribute("aria-modal"); if (el.id === "chat") el.removeAttribute("role"); }
    const anyModal = $$("#chat[aria-modal=true]:not([hidden]), #ctx[aria-modal=true]:not([hidden])").length > 0;
    $$(".shell > .rail, .shell > #main").forEach((x) => (anyModal ? x.setAttribute("inert", "") : x.removeAttribute("inert")));
  }
  function setOpen(on) {
    const p = pane(); if (!p) return;
    p.hidden = !on;
    const ctx = $("#ctx"); if (ctx) ctx.hidden = on;
    const shell = $(".shell");
    if (shell) { shell.classList.toggle("chat-open", on); if (!ctx) shell.classList.toggle("two", !on); }
    $$('[aria-controls="chat"]').forEach((b) => b.setAttribute("aria-expanded", String(on)));
    sheet(p, on);
  }
  function openChat(from, focusEl) {
    if (!pane()) return;
    opener = from || $("#chat-toggle");
    // Context strips ("You are asking about…", the memory quote) only belong to the control that opened them.
    $$("[data-about-for]", pane()).forEach((el) => (el.hidden = !(opener && el.dataset.aboutFor === opener.id)));
    const strip = $("#from-chat"); if (strip) strip.hidden = true;
    const sh = $(".shell"); if (sh) sh.classList.remove("ctx-pushed");
    sheet($("#ctx"), false);
    setOpen(true);
    const log = $('[role="log"]', pane()), hit = log && $(".hit", log);
    if (focusEl) focusEl.scrollIntoView({ block: "nearest" });
    else if (log) log.scrollTop = hit && opener && opener.id === "open-in-chat" ? hit.offsetTop - log.offsetTop - 60 : log.scrollHeight;
    const f = focusEl || $("textarea", pane()); if (f) f.focus();
  }
  function closeChat() {
    const sh = $(".shell"); if (sh) sh.classList.remove("ctx-pushed");
    setOpen(false);
    const back = opener || document.getElementById(pane().dataset.returnFocus || "") || $("#chat-toggle");
    if (back) back.focus();
  }
  document.addEventListener("click", (e) => {
    const t = e.target.closest && e.target.closest('[aria-controls="chat"], #chat-close, [data-close-chat]');
    if (!t) return;
    e.preventDefault();
    if (t.matches("#chat-close, [data-close-chat]")) return closeChat();
    if (chatOpen() && t.id === "chat-toggle") return closeChat();
    openChat(t);
  });

  // 9. Card details always match the selected card. A chat card row opens THAT card's details in the right-hand
  //    slot with a "Back to chat" strip; Back to chat reopens the chat at the same row. Board selection does the same
  //    (without the strip). Details come from <template id="tpl-A-…">, built from the same data as the card.
  const cardOf = (id) => $$(".board .card").find((c) => $(".cid", c) && $(".cid", c).textContent === id);
  showDetailsRef = (id, row) => showDetails(id, row);
  function backToChat() {
    const id = $("#from-chat").dataset.row;
    openChat($("#chat-toggle"), id && $(`#chat [data-card="${id}"]`));
  }
  function showDetails(id, row) {
    const tpl = document.getElementById("tpl-" + id), panel = $("#ctx-panel");
    $$(".board .card").forEach((c) => { c.classList.remove("is-selected"); c.removeAttribute("aria-current"); });
    const c = cardOf(id);
    if (c) { c.classList.add("is-selected"); c.setAttribute("aria-current", "true"); c.scrollIntoView({ block: "nearest" }); }
    if (!tpl || !panel) return;
    panel.innerHTML = tpl.innerHTML;
    $$("#ctx [role=tab]").forEach((t) => { const on = t.id === "tab-details"; t.setAttribute("aria-selected", String(on)); t.tabIndex = on ? 0 : -1; });
    panel.setAttribute("aria-labelledby", "tab-details");
    const strip = $("#from-chat"), fromRow = row && row !== "board";
    if (strip) {
      strip.hidden = !row;
      strip.dataset.row = fromRow ? id : "";
      const t = $(".fc-text", strip);
      // The strip names the message the row sits in (writer and time come from the row itself).
      const chatName = ($("#chat-h") || {}).textContent || "the chat";
      if (t) t.textContent = fromRow ? `Opened from ${chatName}: ${row.dataset.src || "a message"}.` : `You picked ${id} on the board. The chat is one click away.`;
    }
    if (row) {
      setOpen(false);
      const sh = $(".shell"); if (sh) sh.classList.add("ctx-pushed");
      sheet($("#ctx"), true);
      const h = $("h2", panel);
      if (h && (fromRow || narrow())) h.focus();
    }
  }
  document.addEventListener("click", (e) => {
    const r = e.target.closest && e.target.closest("#chat [data-card]");
    if (r) { e.preventDefault(); return showDetails(r.dataset.card, r); }
    const back = e.target.closest && e.target.closest("[data-back-chat]");
    if (back) return backToChat();
    const c = e.target.closest && e.target.closest(".board .card");
    if (c && !e.target.closest("button")) showDetails($(".cid", c).textContent, chatOpen() ? "board" : null);
  });
  // Tapping the dimmed area closes the sheet (touch users have no Esc): the chat closes, a card sheet goes back to the chat.
  document.addEventListener("click", (e) => {
    if (!e.target.matches || !e.target.matches(".chat-scrim")) return;
    const strip = $("#from-chat");
    if ($(".shell.ctx-pushed") && strip && !strip.hidden) backToChat(); else if (chatOpen()) closeChat();
  });
  // Tablet (700–1099 px): the Details button opens the context panel as B's modal sheet; Hide or Esc closes it.
  document.addEventListener("click", (e) => {
    const d = e.target.closest && e.target.closest("#details-btn, #ctx .hide");
    if (!d) return;
    const fcs = $("#from-chat");
    if (d.matches(".hide") && fcs && !fcs.hidden) return backToChat();
    const sh = $(".shell"), ctx = $("#ctx");
    if (!sh || !ctx) return;
    if (d.id === "details-btn") {
      if (chatOpen()) setOpen(false);
      ctx.hidden = false;
      if (!$(".tablet-scrim")) { const sc = document.createElement("div"); sc.className = "tablet-scrim"; sc.setAttribute("aria-hidden", "true"); sh.insertBefore(sc, ctx); }
      $(".tablet-scrim").hidden = false;
      sh.classList.add("panel-open");
      ctx.setAttribute("role", "dialog"); ctx.setAttribute("aria-modal", "true"); ctx.dataset.returnFocus = "details-btn";
      $$(".shell > .rail, .shell > #main").forEach((x) => x.setAttribute("inert", ""));
      d.setAttribute("aria-expanded", "true");
      $(".hide", ctx).focus();
    } else if (sh.classList.contains("panel-open")) {
      sh.classList.remove("panel-open");
      ctx.removeAttribute("aria-modal");
      $$("[inert]").forEach((x) => x.removeAttribute("inert"));
      const b = $("#details-btn"); if (b) { b.setAttribute("aria-expanded", "false"); b.focus(); }
    }
  });
  // Esc inside the chat (also from the message box; the draft stays) closes it. Dialogs keep their own Esc.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const m = modal();
    const strip = $("#from-chat");
    if (strip && !strip.hidden && $("#ctx").contains(document.activeElement) && (!m || m.id === "ctx")) { e.preventDefault(); return backToChat(); }
    if (m && m.id !== "chat") return;
    if (!chatOpen() || !pane().contains(document.activeElement)) return;
    e.preventDefault();
    closeChat();
  });
  // Rows in chat point at cards: focus or hover outlines the card on the board (dashed), so the link is visible.
  // The outline goes away when focus or the pointer leaves the row.
  const clearLinked = () => $$(".board .card.is-linked").forEach((c) => c.classList.remove("is-linked"));
  ["focusin", "mouseover"].forEach((ev) => document.addEventListener(ev, (e) => {
    const r = e.target.closest && e.target.closest("#chat [data-card]");
    if (r) { clearLinked(); const c = cardOf(r.dataset.card); if (c) c.classList.add("is-linked"); }
  }));
  ["focusout", "mouseout"].forEach((ev) => document.addEventListener(ev, (e) => {
    const r = e.target.closest && e.target.closest("#chat [data-card]");
    if (r && !(e.relatedTarget && r.contains(e.relatedTarget))) clearLinked();
  }));
  addEventListener("DOMContentLoaded", () => {
    // Chat logs open at the newest message, or at the message a memory result pointed to.
    $$('.chatpane [role="log"], .m-chat:not(.m-settings-sheet) .m-chat-log').forEach((l) => {
      const hit = $(".hit", l);
      if (hit) l.scrollTop = hit.offsetTop - l.offsetTop - 60; else l.scrollTop = l.scrollHeight;
    });
    if (chatOpen()) sheet(pane(), true);
    // A card drawn open from the chat (06f, 06g) is the pushed slot at 1024–1099 px and a modal sheet below 1024 px.
    const fc = $("#from-chat");
    if ($(".shell.ctx-pushed") && fc && !fc.hidden) sheet($("#ctx"), true);
    // An approval confirm opened in place keeps the note field above it in view.
    const cf = $("#confirm-send:not([hidden])");
    if (cf) { const body = $("#ctx .ctx-body"); if (body) body.scrollTop = body.scrollHeight; }
    // Phone Settings opened from Needs me goes back there.
    const sb = $("#m-settings-back");
    if (sb && /from=harbor/.test(location.search)) sb.href = "m2b-needs-me-harbor.html#m-settings";
    const mb = $("#m-chat-back");
    if (mb && /from=needs/.test(location.search)) { mb.lastChild.textContent = "Back to Needs me"; mb.href = "m2-needs-me.html#m-chat-tab"; }
    // A link may name the element to return to (#m-chat-tab, #row-A-135): focus it, so Back keeps your place.
    const target = location.hash.length > 1 && document.getElementById(location.hash.slice(1));
    if (target) { target.scrollIntoView({ block: "center" }); target.focus(); }
    // Restore the single-key switch from storage so the sheet never shows On while keys are off.
    const sw = $('[role="switch"]');
    if (sw && localStorage.getItem("wb-single-keys") === "off") { sw.setAttribute("aria-checked", "false"); const st = $(".state", sw); if (st) st.textContent = "Off"; }
  });

  // 7a. The rail's "Search or do…" opens the palette (its own screen in this static set).
  document.addEventListener("click", (e) => {
    if (e.target.closest && e.target.closest(".cmdk") && !/command-palette/.test(location.pathname)) location.href = "11-command-palette.html";
    const sw = e.target.closest && e.target.closest('[role="switch"]');
    if (sw) {
      const on = sw.getAttribute("aria-checked") !== "true";
      sw.setAttribute("aria-checked", String(on));
      const st = $(".state", sw); if (st) st.textContent = on ? "On" : "Off";
      localStorage.setItem("wb-single-keys", on ? "on" : "off");
    }
  });

  // 7. Buttons that reveal an inline step (e.g. the external-send confirm) move focus into it.
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-reveal]");
    if (!b) return;
    const t = document.getElementById(b.dataset.reveal);
    t.hidden = false;
    b.hidden = true;
    const body = t.closest("#ctx") && $("#ctx .ctx-body"); if (body) body.scrollTop = body.scrollHeight;
    const f = $("[data-autofocus]", t) || focusables(t)[0];
    if (f) f.focus();
  });
})();
