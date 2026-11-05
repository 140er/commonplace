/* Workbench keyboard behaviour for the static screens (design reference, no network).
   Demonstrates: initial focus, roving tabindex on the board, tablists, the palette
   listbox, modal focus trap + Esc + focus return, and the single-key shortcut rules. */
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
    if (e.key === "Escape") {
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
    const target = keys[e.key.toLowerCase()] && $(keys[e.key.toLowerCase()]);
    if (target) { e.preventDefault(); target.focus(); }
  });

  // 8. Chat (B + chat): one toggle opens and closes the project chat in the right-hand slot.
  //    Opening hides the context panel, moves focus into the message box and sets aria-expanded on every opener;
  //    closing brings the context panel back and returns focus to the control that opened it.
  const pane = () => $("#chat");
  const chatOpen = () => pane() && !pane().hidden;
  let opener = null;
  function setOpen(on) {
    const p = pane(); if (!p) return;
    p.hidden = !on;
    const ctx = $("#ctx"); if (ctx) ctx.hidden = on;
    const shell = $(".shell"); if (shell) shell.classList.toggle("chat-open", on);
    $$('[aria-controls="chat"]').forEach((b) => b.setAttribute("aria-expanded", String(on)));
  }
  function openChat(from) {
    if (!pane()) return;
    opener = from || $("#chat-toggle");
    setOpen(true);
    const log = $('[role="log"]', pane()); if (log) log.scrollTop = log.scrollHeight;
    const box = $("textarea", pane()); if (box) box.focus();
  }
  function closeChat() {
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
  // Esc inside the chat (also from the message box; the draft stays) closes it. Dialogs keep their own Esc.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || modal() || !chatOpen() || !pane().contains(document.activeElement)) return;
    e.preventDefault();
    closeChat();
  });
  // Rows in chat point at cards: focus or hover outlines the card on the board (dashed), so the link is visible.
  const mark = (row, on) => {
    const id = row.dataset.card;
    $$(".board .card").forEach((c) => { if (c.querySelector(".cid") && c.querySelector(".cid").textContent === id) c.classList.toggle("is-linked", on); });
  };
  ["focusin", "mouseover"].forEach((ev) => document.addEventListener(ev, (e) => { const r = e.target.closest && e.target.closest("[data-card]"); if (r) { $$(".card.is-linked").forEach((c) => c.classList.remove("is-linked")); mark(r, true); } }));
  // Selecting a chat row selects its card on the board (focus stays in the chat).
  document.addEventListener("click", (e) => {
    const r = e.target.closest && e.target.closest("#chat [data-card]");
    if (!r) return;
    e.preventDefault();
    $$(".board .card").forEach((c) => {
      const on = c.querySelector(".cid") && c.querySelector(".cid").textContent === r.dataset.card;
      c.classList.toggle("is-selected", on);
      if (on) { c.setAttribute("aria-current", "true"); c.scrollIntoView({ block: "nearest" }); } else c.removeAttribute("aria-current");
    });
  });
  // Chat logs open at the newest message, or at the message a memory result pointed to.
  addEventListener("DOMContentLoaded", () => {
    $$('.chatpane [role="log"], .m-chat-log').forEach((l) => {
      const hit = $(".hit", l);
      if (hit) l.scrollTop = hit.offsetTop - l.offsetTop - 60; else l.scrollTop = l.scrollHeight;
    });
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
    const f = $("[data-autofocus]", t) || focusables(t)[0];
    if (f) f.focus();
  });
})();
