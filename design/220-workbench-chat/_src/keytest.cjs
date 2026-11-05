// Live keyboard check of the static screens (playwright-core + local Chrome; no network).
// Usage: node _src/keytest.cjs <path-to-playwright-core>   (uses /usr/bin/google-chrome)
const { chromium } = require(process.argv[2]);
const path = require("path");
const D = path.resolve(__dirname, "..", "screens");
const cases = [
  // [screen, width, height, expected initial focus id/selector, expected return id after Esc]
  ["02-create-project", 1440, 900, "input.field", "new-project"],
  ["03c-connect-agent", 1440, 900, "#cn", "connect-btn"],
  ["03c2-connect-waiting", 1440, 900, ".snippet button", "connect-btn"],
  ["03d-connect-done", 1440, 900, ".dialog .btn.primary", "connect-btn"],
  ["03e-connect-failed", 1440, 900, ".dialog .btn.primary", "connect-btn"],
  ["10c-disconnect-orchestrator", 1440, 900, ".dialog .btn", "disc-orch"],
  ["10d-disconnect-last-agent", 1440, 900, ".dialog .btn", "disc-orch"],
  ["11-command-palette", 1440, 900, ".palette input", "cmdk"],
  ["11b-command-palette-card", 1440, 900, ".palette input", "cmdk"],
  ["12-shortcuts", 1440, 900, ".switch", "shortcuts-link"],
  ["04c-board-tablet-panel", 1024, 768, ".hide", "details-btn"],
  ["m4-add-card", 390, 844, ".sheet input", "fab"],
  ["m5-approve-confirm", 390, 844, ".sheet .btn", "m-approve"],
  ["m9-card-menu", 390, 844, ".sheet .opt", "more-A-121"],
  ["m10-chat", 390, 844, "#m-chat-back", "m-chat-tab"],
];
(async () => {
  const browser = await chromium.launch({ executablePath: "/usr/bin/google-chrome", headless: true });
  let fails = 0;
  for (const [name, w, h, first, back] of cases) {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    await page.goto("file://" + D + "/" + name + ".html");
    await page.waitForTimeout(150);
    const initialOk = await page.evaluate((sel) => document.activeElement === document.querySelector(sel), first);
    // Tab 25 times: focus must stay inside the modal
    let trapped = true;
    for (let i = 0; i < 25; i++) {
      await page.keyboard.press("Tab");
      trapped = trapped && (await page.evaluate(() => !!document.activeElement.closest('[aria-modal="true"]')));
    }
    await page.keyboard.press("Escape");
    await page.waitForTimeout(50);
    const after = await page.evaluate(() => {
      const vis = (el) => el && getComputedStyle(el).display !== "none" && el.getBoundingClientRect().width > 0;
      const modals = [...document.querySelectorAll('[aria-modal="true"]')].filter(vis).length;
      const scrims = [...document.querySelectorAll(".scrim, .tablet-scrim")].filter(vis).length;
      return { modals, scrims, inert: document.querySelectorAll("[inert]").length, focus: document.activeElement.id };
    });
    const ok = initialOk && trapped && after.modals === 0 && after.scrims === 0 && after.inert === 0 && after.focus === back;
    if (!ok) fails++;
    console.log(`${ok ? "PASS" : "FAIL"} ${name}: initial=${initialOk} trapped=${trapped} visibleModals=${after.modals} visibleScrims=${after.scrims} inert=${after.inert} focus=#${after.focus} (want #${back})`);
    await page.close();
  }
  // Board: roving tabindex with arrows, Done reachable
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto("file://" + D + "/04-board.html");
  await page.focus("article[aria-label^='A-121']");
  const seq = [];
  for (const k of ["ArrowDown", "ArrowRight", "ArrowRight", "ArrowDown", "ArrowLeft"]) {
    await page.keyboard.press(k);
    seq.push(await page.evaluate(() => (document.activeElement.getAttribute("aria-label") || document.activeElement.textContent).trim().slice(0, 14)));
  }
  const tabStops = await page.evaluate(() => document.querySelectorAll('.board [tabindex="0"]').length);
  console.log("board arrows: " + seq.join(" → ") + ` | board tab stops: ${tabStops}`);
  // Palette: ArrowDown moves the active option
  await page.goto("file://" + D + "/11-command-palette.html");
  await page.waitForTimeout(100);
  await page.keyboard.press("ArrowDown");
  const act = await page.evaluate(() => document.querySelector(".palette input").getAttribute("aria-activedescendant") + " / " + document.querySelector('[role=option][aria-selected=true]').textContent.trim().slice(0, 30));
  console.log("palette ArrowDown → " + act);
  // Needs me: A and D do nothing
  await page.goto("file://" + D + "/07-needs-me.html");
  await page.waitForTimeout(100);
  await page.keyboard.press("a"); await page.keyboard.press("d");
  const confirmShown = await page.evaluate(() => !document.getElementById("confirm-send").hidden);
  console.log(`needs-me: A/D pressed, confirm shown=${confirmShown} (want false)`);
  if (confirmShown) fails++;
  // Chat (B + chat): the toggle, "/", Esc, Close chat, contextual openers, and the chat-row → card link.
  const st = () => page.evaluate(() => ({
    open: !document.getElementById("chat").hidden,
    ctx: document.getElementById("ctx") ? !document.getElementById("ctx").hidden : null,
    exp: [...document.querySelectorAll('[aria-controls="chat"]')].map((b) => b.getAttribute("aria-expanded")).join(","),
    focus: document.activeElement.id || document.activeElement.tagName,
    inChat: !!document.activeElement.closest("#chat"),
  }));
  const check = (label, got, want) => {
    const ok = Object.keys(want).every((k) => got[k] === want[k]);
    if (!ok) fails++;
    console.log(`${ok ? "PASS" : "FAIL"} ${label}: ${JSON.stringify(got)}`);
  };
  await page.goto("file://" + D + "/04-board.html");
  await page.waitForTimeout(100);
  check("04 closed at load", await st(), { open: false, ctx: true, exp: "false" });
  await page.focus("#chat-toggle"); await page.keyboard.press("Enter");
  check("04 toggle Enter opens", await st(), { open: true, ctx: false, exp: "true", inChat: true });
  await page.keyboard.press("Escape");
  check("04 Esc in message box closes, focus back on toggle", await st(), { open: false, ctx: true, exp: "false", focus: "chat-toggle" });
  await page.focus("article[aria-label^='A-121']"); await page.keyboard.press("/");
  check("04 '/' from the board opens and focuses the box", await st(), { open: true, exp: "true", focus: "ci" });
  await page.click("#chat-close");
  check("04 Close chat button", await st(), { open: false, ctx: true, exp: "false", focus: "chat-toggle" });
  await page.focus("#chat-toggle"); await page.keyboard.press("Enter"); await page.focus("#chat-toggle"); await page.keyboard.press("Space");
  check("04 toggle again closes", await st(), { open: false, exp: "false", focus: "chat-toggle" });
  await page.goto("file://" + D + "/07-needs-me.html"); await page.waitForTimeout(100);
  await page.click("#ask-question");
  check("07 Ask a question opens chat (both openers expanded)", await st(), { open: true, ctx: false, exp: "true,true", inChat: true });
  await page.click("[data-close-chat]");
  check("07 Back to the approval", await st(), { open: false, ctx: true, exp: "false,false", focus: "ask-question" });
  await page.goto("file://" + D + "/09-memory.html"); await page.waitForTimeout(100);
  await page.click("#open-in-chat"); await page.keyboard.press("Escape");
  check("09 Open in chat, then Esc", await st(), { open: false, ctx: true, focus: "open-in-chat" });
  await page.goto("file://" + D + "/06c-chat-answer.html"); await page.waitForTimeout(100);
  const linked = async () => page.evaluate(() => [...document.querySelectorAll(".board .card.is-linked .cid")].map((c) => c.textContent).join(","));
  await page.focus('#chat [data-card="A-135"]');
  const l1 = await linked();
  await page.focus('#chat [data-card="A-118"]');
  const l2 = await linked();
  const lok = l1 === "A-135" && l2 === "A-118";
  if (!lok) fails++;
  console.log(`${lok ? "PASS" : "FAIL"} 06c chat row focus outlines its card: A-135 row → ${l1}; A-118 row → ${l2}`);
  // Round 2: chat card row → that card's details (with Back to chat); details always match the selected card.
  const det = () => page.evaluate(() => {
    const sel = document.querySelector(".board .card.is-selected");
    const ctx = document.getElementById("ctx");
    return {
      chat: !document.getElementById("chat").hidden, ctx: !ctx.hidden,
      ctxId: ctx.querySelector(".detail-h .cid").textContent, ctxTitle: ctx.querySelector(".detail-h h2").textContent.trim(),
      selId: sel && sel.querySelector(".cid").textContent, selTitle: sel && sel.querySelector("h3").textContent.trim(),
      strip: !document.getElementById("from-chat").hidden, focus: document.activeElement.tagName + (document.activeElement.dataset.card ? ":" + document.activeElement.dataset.card : ""),
    };
  });
  const agree = (label, d, id, extra = {}) => {
    const ok = d.ctxId === id && d.selId === id && d.ctxTitle === d.selTitle && Object.keys(extra).every((k) => d[k] === extra[k]);
    if (!ok) fails++;
    console.log(`${ok ? "PASS" : "FAIL"} ${label}: ${JSON.stringify(d)}`);
  };
  await page.goto("file://" + D + "/06c-chat-answer.html"); await page.waitForTimeout(100);
  await page.click('#chat [data-card="A-134"]');
  agree("06c click A-134 row → A-134 details, Back to chat strip", await det(), "A-134", { chat: false, ctx: true, strip: true, focus: "H2" });
  await page.click("[data-back-chat]");
  const back1 = await det();
  const bok = back1.chat && back1.focus === "A:A-134";
  if (!bok) fails++;
  console.log(`${bok ? "PASS" : "FAIL"} 06c Back to chat → chat open, focus on the same row: ${JSON.stringify(back1)}`);
  await page.focus('#chat [data-card="A-118"]'); await page.keyboard.press("Enter");
  agree("06c Enter on A-118 row → A-118 details", await det(), "A-118", { chat: false, strip: true });
  await page.click("#chat-toggle"); await page.click("#chat-toggle");
  agree("06c open and close chat again → still A-118", await det(), "A-118", { chat: false, ctx: true, strip: false });
  await page.click('.board .card[aria-label^="A-124"] h3');
  agree("06c click a board card → its details, no strip", await det(), "A-124", { strip: false });
  await page.goto("file://" + D + "/06f-chat-card-details.html"); await page.waitForTimeout(100);
  agree("06f static: A-134 details match the selected card", await det(), "A-134", { chat: false, strip: true });
  await page.goto("file://" + D + "/06f2-chat-card-details-a118.html"); await page.waitForTimeout(100);
  agree("06f2 static: A-118 details match the selected card", await det(), "A-118", { chat: false, strip: true });
  // Tablet: a chat row opens the card in the pushed slot, visible next to the board
  const tp = await browser.newPage({ viewport: { width: 1024, height: 768 } });
  await tp.goto("file://" + D + "/06c-chat-answer.html"); await tp.waitForTimeout(100);
  await tp.click('#chat [data-card="A-135"]');
  const tv = await tp.evaluate(() => { const c = document.getElementById("ctx"); const r = c.getBoundingClientRect(); return { shown: getComputedStyle(c).display !== "none" && r.width > 300, id: c.querySelector(".detail-h .cid").textContent, boardRight: document.querySelector(".center").getBoundingClientRect().right, ctxLeft: r.left }; });
  const tok = tv.shown && tv.id === "A-135" && tv.ctxLeft >= tv.boardRight - 1;
  if (!tok) fails++;
  console.log(`${tok ? "PASS" : "FAIL"} 1024 px: chat row A-135 → its details beside the board (not over it): ${JSON.stringify(tv)}`);
  await tp.close();
  // focusout / mouseout clear the dashed outline
  await page.goto("file://" + D + "/06c-chat-answer.html"); await page.waitForTimeout(100);
  await page.focus('#chat [data-card="A-135"]'); const f1 = await linked();
  await page.focus("#ci"); const f2 = await linked();
  await page.hover('#chat [data-card="A-118"]'); const m1 = await linked();
  await page.mouse.move(600, 850); const m2 = await linked();
  const ook = f1 === "A-135" && f2 === "" && m1 === "A-118" && m2 === "";
  if (!ook) fails++;
  console.log(`${ook ? "PASS" : "FAIL"} outline follows focus and pointer: focus A-135 → ${f1}; focus leaves → "${f2}"; hover A-118 → ${m1}; pointer leaves → "${m2}"`);
  // Needs me and Memory have the project Chat control; context strips only for their own opener
  const strips = () => page.evaluate(() => [...document.querySelectorAll("#chat [data-about-for]")].map((e) => e.hidden ? "hidden" : "shown").join(","));
  await page.goto("file://" + D + "/07-needs-me.html"); await page.waitForTimeout(100);
  await page.click("#chat-toggle");
  const s1 = await st(); const sa = await strips();
  await page.keyboard.press("Escape");
  await page.focus("h2[data-autofocus]"); await page.keyboard.press("r");
  const s2 = await st(); const sb = await strips();
  const nok = s1.open && sa === "hidden" && s2.open && sb === "shown" && s2.exp === "true,true";
  if (!nok) fails++;
  console.log(`${nok ? "PASS" : "FAIL"} 07 Atlas chat toggle opens without the approval strip (${sa}); R opens it with the strip (${sb}), expanded=${s2.exp}`);
  await page.goto("file://" + D + "/09-memory.html"); await page.waitForTimeout(100);
  await page.click("#chat-toggle"); const ma = await strips(); await page.keyboard.press("Escape");
  await page.click("#open-in-chat"); const mb = await strips();
  const mok = ma === "hidden,hidden" && mb === "shown,shown";
  if (!mok) fails++;
  console.log(`${mok ? "PASS" : "FAIL"} 09 Atlas chat toggle: quote and strip ${ma}; Open in chat: ${mb}`);
  await page.goto("file://" + D + "/03-board-empty.html"); await page.waitForTimeout(100);
  const cls = () => page.evaluate(() => document.querySelector(".shell").className);
  const c0 = await cls(); await page.click("#chat-toggle"); const c1 = await cls(); await page.keyboard.press("Escape"); const c2 = await cls();
  const eok = /two/.test(c0) && /chat-open/.test(c1) && !/two/.test(c1) && /two/.test(c2) && !/chat-open/.test(c2);
  if (!eok) fails++;
  console.log(`${eok ? "PASS" : "FAIL"} 03 starts closed (${c0}); open (${c1}); closed again (${c2})`);
  // Mobile: each Show goes to its own card; Back keeps your place
  const mp = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const res = [];
  for (const id of ["A-134", "A-135", "A-118"]) {
    await mp.goto("file://" + D + "/m10-chat.html"); await mp.waitForTimeout(100);
    await mp.click(`#row-${id}`); await mp.waitForTimeout(150);
    const r = await mp.evaluate(() => ({ sel: document.querySelector(".card.is-selected .cid").textContent, col: document.querySelector('[role=tab][aria-selected=true]').textContent.trim(), focus: document.activeElement.getAttribute("aria-label") || "" }));
    await mp.click(".m-frombar a"); await mp.waitForTimeout(150);
    const back = await mp.evaluate(() => document.activeElement.id);
    const ok = r.sel === id && r.focus.startsWith(id) && back === "row-" + id && r.col.startsWith(id === "A-118" ? "Doing" : "Todo");
    if (!ok) fails++;
    res.push(`${ok ? "PASS" : "FAIL"} m10 Show ${id} → ${r.col}, selected ${r.sel}; Back to chat → focus #${back}`);
  }
  await mp.goto("file://" + D + "/m10-chat.html"); await mp.waitForTimeout(100);
  await mp.click("#m-chat-back"); await mp.waitForTimeout(150);
  const tb = await mp.evaluate(() => document.activeElement.id);
  if (tb !== "m-chat-tab") fails++;
  res.push(`${tb === "m-chat-tab" ? "PASS" : "FAIL"} m10 Back to board → focus #${tb} (want #m-chat-tab)`);
  console.log(res.join("\n"));
  // ---- Round 3
  const ok3 = (label, cond, info) => { if (!cond) fails++; console.log(`${cond ? "PASS" : "FAIL"} ${label}: ${JSON.stringify(info)}`); };
  // Composer label follows the project, from the same data as heading and log
  for (const [scr, proj] of [["07-needs-me", "Atlas"], ["08-needs-me-done", "Harbor"]]) {
    await page.goto("file://" + D + "/" + scr + ".html"); await page.waitForTimeout(100);
    await page.click("#chat-toggle");
    const n = await page.evaluate(() => ({ label: document.querySelector('label[for="ci"]').textContent, head: document.getElementById("chat-h").textContent, log: document.querySelector('#chat [role="log"]').getAttribute("aria-label"), toggle: document.getElementById("chat-toggle").textContent }));
    ok3(`${scr}: composer, heading, log and control all name ${proj}`, n.label === `Message to ${proj} chat` && n.head === `${proj} chat` && n.log.includes(proj) && n.toggle.includes(proj), n);
  }
  // Board card chosen while the chat is open → its details in the same slot, board-origin strip, Back to chat
  await page.goto("file://" + D + "/06c-chat-answer.html"); await page.waitForTimeout(100);
  await page.click('.board .card[aria-label^="A-121"] h3');
  const bo = await det(); const bt = await page.evaluate(() => document.querySelector("#from-chat .fc-text").textContent);
  ok3("06c chat open, click board card A-121 → A-121 details, board-origin strip", bo.ctxId === "A-121" && bo.selId === "A-121" && bo.strip && !bo.chat && /on the board/.test(bt), { ...bo, bt });
  await page.click("[data-back-chat]");
  const bb = await page.evaluate(() => ({ chat: !document.getElementById("chat").hidden, focus: document.activeElement.id }));
  ok3("06c board-origin Back to chat → chat open, cursor in the box", bb.chat && bb.focus === "ci", bb);
  await page.focus('.board .card[aria-label^="A-124"]'); await page.keyboard.press("Enter");
  const be = await det();
  ok3("06c chat open, Enter on board card A-124 → A-124 details with strip", be.ctxId === "A-124" && be.selId === "A-124" && be.strip && !be.chat, be);
  // ] closes the chat from outside a field
  await page.goto("file://" + D + "/04-board.html"); await page.waitForTimeout(100);
  await page.click("#chat-toggle"); await page.focus("#chat-close"); await page.keyboard.press("]");
  const br = await st();
  ok3("04 ] closes the chat", !br.open && br.exp === "false", br);
  // Below 1024 px the chat is a modal sheet: dialog, board inert, Tab trapped, Esc returns
  const tab = await browser.newPage({ viewport: { width: 768, height: 1024 } });
  await tab.goto("file://" + D + "/04-board.html"); await tab.waitForTimeout(100);
  await tab.click("#chat-toggle");
  const q0 = await tab.evaluate(() => ({ modal: document.getElementById("chat").getAttribute("aria-modal"), role: document.getElementById("chat").getAttribute("role"), inertMain: document.getElementById("main").hasAttribute("inert"), scrim: getComputedStyle(document.querySelector(".chat-scrim")).display }));
  let trapped = true;
  for (let i = 0; i < 25; i++) { await tab.keyboard.press("Tab"); trapped = trapped && (await tab.evaluate(() => !!document.activeElement.closest("#chat"))); }
  await tab.keyboard.press("Escape"); await tab.keyboard.press("Escape");
  const q1 = await tab.evaluate(() => ({ open: !document.getElementById("chat").hidden, inert: document.querySelectorAll("[inert]").length, focus: document.activeElement.id }));
  ok3("768 px: chat is a modal sheet (dialog, scrim, board inert), Tab stays inside, Esc closes to the control", q0.modal === "true" && q0.role === "dialog" && q0.inertMain && q0.scrim === "block" && trapped && !q1.open && q1.inert === 0 && q1.focus === "chat-toggle", { ...q0, trapped, ...q1 });
  await tab.goto("file://" + D + "/06c-chat-answer.html"); await tab.waitForTimeout(100);
  const q2 = await tab.evaluate(() => ({ modal: document.getElementById("chat").getAttribute("aria-modal"), inert: document.getElementById("main").hasAttribute("inert") }));
  await tab.click('#chat [data-card="A-134"]');
  const q3 = await tab.evaluate(() => ({ ctxModal: document.getElementById("ctx").getAttribute("aria-modal"), id: document.querySelector("#ctx .detail-h .cid").textContent, inert: document.getElementById("main").hasAttribute("inert"), focus: document.activeElement.tagName }));
  await tab.keyboard.press("Escape");
  const q4 = await tab.evaluate(() => ({ chat: !document.getElementById("chat").hidden, focus: document.activeElement.dataset.card || document.activeElement.id }));
  ok3("768 px: 06c drawn open is modal; a chat row opens its details as a modal sheet; Esc goes back to the same row", q2.modal === "true" && q2.inert && q3.ctxModal === "true" && q3.id === "A-134" && q3.inert && q3.focus === "H2" && q4.chat && q4.focus === "A-134", { q2, q3, q4 });
  await tab.close();
  // Below 700 px the chat is a full-screen sheet (not stacked under the board)
  const ph = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await ph.goto("file://" + D + "/06c-chat-answer.html"); await ph.waitForTimeout(100);
  const p0 = await ph.evaluate(() => { const r = document.getElementById("chat").getBoundingClientRect(); return { top: r.top, left: r.left, w: r.width, h: r.height, modal: document.getElementById("chat").getAttribute("aria-modal"), boardInert: document.getElementById("main").hasAttribute("inert") }; });
  ok3("390 px: 06c chat is a full-screen modal sheet from the top", p0.top === 0 && p0.left === 0 && p0.w === 390 && p0.h >= 840 && p0.modal === "true" && p0.boardInert, p0);
  // Phone ask paths and returns
  const go = async (u) => { await ph.goto("file://" + D + "/" + u); await ph.waitForTimeout(120); };
  const here = () => ph.evaluate(() => ({ page: location.pathname.split("/").pop(), focus: document.activeElement.id, strip: (document.querySelector(".m-about") || {}).textContent || "", back: (document.getElementById("m-chat-back") || {}).textContent || "" }));
  await go("m3-needs-me-item.html"); await ph.click("#m-ask"); await ph.waitForTimeout(150); const a1 = await here();
  await ph.click("#m-chat-back"); await ph.waitForTimeout(150); const a2 = await here();
  ok3("q3 Ask a question → m10b with the approval strip; Back to the approval → q3, focus on Ask", a1.page === "m10b-chat-ask-approval.html" && /You are asking about/.test(a1.strip) && /Back to the approval/.test(a1.back) && a2.page === "m3-needs-me-item.html" && a2.focus === "m-ask", { a1, a2 });
  await go("m8-send-too-late.html"); await ph.click("#m-ask-orch"); await ph.waitForTimeout(150); const b1 = await here();
  await ph.click("#m-chat-back"); await ph.waitForTimeout(150); const b2 = await here();
  ok3("m8 Ask the orchestrator → m10c with the released strip; back → m8, focus on the button", b1.page === "m10c-chat-ask-released.html" && /can't be recalled/.test(b1.strip) && b2.page === "m8-send-too-late.html" && b2.focus === "m-ask-orch", { b1, b2 });
  await go("m2-needs-me.html"); await ph.click("#m-chat-tab"); await ph.waitForTimeout(150); const n1 = await here();
  await ph.click("#m-chat-back"); await ph.waitForTimeout(150); const n2 = await here();
  ok3("q2 Chat tab → chat says Back to Needs me; returns to Needs me, focus on the Chat tab", /Back to Needs me/.test(n1.back) && n2.page === "m2-needs-me.html" && n2.focus === "m-chat-tab", { n1, n2 });
  await go("m1-board.html"); const sl = await ph.evaluate(() => document.getElementById("m-settings").textContent.trim());
  await ph.click("#m-settings"); await ph.waitForTimeout(150); const d1 = await here();
  await ph.click("#m-settings-back"); await ph.waitForTimeout(150); const d2 = await here();
  ok3("q1 labelled Settings → m13 sheet; back → q1, focus on Settings", sl === "Settings" && d1.page === "m13-settings.html" && d2.page === "m1-board.html" && d2.focus === "m-settings", { sl, d1, d2 });
  // ---- Round 4
  const r4 = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const at4 = (pg4) => pg4.evaluate(() => ({ page: location.pathname.split("/").pop(), focus: document.activeElement.id }));
  await r4.goto("file://" + D + "/06d-chat-error.html"); await r4.waitForTimeout(100);
  const first4 = await r4.evaluate(() => [...document.querySelectorAll("#chat .msg-state.err .acts > *")].map((b) => b.textContent.trim()));
  await r4.click("#restart-agent"); await r4.waitForTimeout(150);
  const s10 = await r4.evaluate(() => ({ page: location.pathname.split("/").pop(), warn: (document.querySelector(".orch-card.warn") || {}).textContent || "", steps: document.querySelectorAll(".restart li").length, check: !!document.querySelector(".restart-box .btn.primary") }));
  await r4.click('a[href^="06d-chat-error.html"]'); await r4.waitForTimeout(150); const s10b = await at4(r4);
  ok3("06d: restart first4, Ask again second → 10f amber 'Not answering since 14:02', 3 steps, Check again → back to the chat on the same button", /restart/.test(first4[0]) && /Ask again/.test(first4[1]) && s10.page === "10f-settings-not-answering.html" && /Not answering since 14:02/.test(s10.warn) && s10.steps === 3 && s10.check && s10b.page === "06d-chat-error.html" && s10b.focus === "restart-agent", { first4, s10, s10b });
  await r4.goto("file://" + D + "/06g-chat-board-pick.html"); await r4.waitForTimeout(100);
  const g4 = await det(); const gt4 = await page.evaluate(() => 1);
  const gtxt = await r4.evaluate(() => document.querySelector("#from-chat .fc-text").textContent);
  const gd = await r4.evaluate(() => { const sel = document.querySelector(".board .card.is-selected"); const ctx = document.getElementById("ctx"); return { ctxId: ctx.querySelector(".detail-h .cid").textContent, ctxTitle: ctx.querySelector(".detail-h h2").textContent.trim(), selId: sel.querySelector(".cid").textContent, selTitle: sel.querySelector("h3").textContent.trim(), strip: !document.getElementById("from-chat").hidden }; });
  ok3("06g static: A-121 picked on the board, its details, board-origin strip", gd.ctxId === "A-121" && gd.selId === "A-121" && gd.ctxTitle === gd.selTitle && gd.strip && /picked A-121 on the board/.test(gtxt), { ...gd, gtxt });
  // 07c: the note field sits above the confirm
  await r4.goto("file://" + D + "/07c-approve-confirm.html"); await r4.waitForTimeout(150);
  const nc = await r4.evaluate(() => { const n = document.querySelector("#ctx .ctx-body textarea").getBoundingClientRect(); const b = document.querySelector("#ctx .ctx-body").getBoundingClientRect(); return { noteBottom: Math.round(n.bottom), bodyBottom: Math.round(b.bottom) }; });
  ok3("07c: the note field is fully visible above the confirm", nc.noteBottom <= nc.bodyBottom, nc);
  await r4.close();
  // Tablet: 06f drawn at4 1024 (pushed slot) and 768 (sheet); Details button; scrim tap
  const t4 = await browser.newPage({ viewport: { width: 1024, height: 768 } });
  await t4.goto("file://" + D + "/06f-chat-card-details.html"); await t4.waitForTimeout(100);
  const f1024 = await t4.evaluate(() => { const c = document.getElementById("ctx"), r = c.getBoundingClientRect(); return { shown: getComputedStyle(c).display !== "none", left: Math.round(r.left), boardRight: Math.round(document.getElementById("main").getBoundingClientRect().right), modal: c.getAttribute("aria-modal") }; });
  ok3("1024 px: 06f shows A-134's details in the pushed slot beside the board", f1024.shown && f1024.left >= f1024.boardRight - 1 && f1024.modal !== "true", f1024);
  await t4.goto("file://" + D + "/04-board.html"); await t4.waitForTimeout(100);
  await t4.click("#details-btn");
  const dOpen = await t4.evaluate(() => ({ shown: getComputedStyle(document.getElementById("ctx")).display !== "none", modal: document.getElementById("ctx").getAttribute("aria-modal"), focus: document.activeElement.className, exp: document.getElementById("details-btn").getAttribute("aria-expanded") }));
  await t4.keyboard.press("Escape");
  const dShut = await t4.evaluate(() => ({ shown: getComputedStyle(document.getElementById("ctx")).display !== "none", inert: document.querySelectorAll("[inert]").length, focus: document.activeElement.id }));
  ok3("1024 px: Details opens B's modal sheet (focus on Hide); Esc closes it back to Details", dOpen.shown && dOpen.modal === "true" && /hide/.test(dOpen.focus) && dOpen.exp === "true" && !dShut.shown && dShut.inert === 0 && dShut.focus === "details-btn", { dOpen, dShut });
  await t4.setViewportSize({ width: 768, height: 1024 });
  await t4.goto("file://" + D + "/06f-chat-card-details.html"); await t4.waitForTimeout(100);
  const f768 = await t4.evaluate(() => ({ modal: document.getElementById("ctx").getAttribute("aria-modal"), inert: document.getElementById("main").hasAttribute("inert"), id: document.querySelector("#ctx .detail-h .cid").textContent }));
  await t4.mouse.click(60, 500); await t4.waitForTimeout(50);
  const f768b = await t4.evaluate(() => ({ chat: !document.getElementById("chat").hidden, focus: document.activeElement.dataset.card || document.activeElement.id }));
  await t4.mouse.click(60, 500); await t4.waitForTimeout(50);
  const f768c = await t4.evaluate(() => ({ chat: !document.getElementById("chat").hidden, inert: document.querySelectorAll("[inert]").length }));
  ok3("768 px: 06f is a modal card sheet; tapping the dimmed area goes back to the chat (same row), tapping again closes the chat", f768.modal === "true" && f768.inert && f768.id === "A-134" && f768b.chat && f768b.focus === "A-134" && !f768c.chat && f768c.inert === 0, { f768, f768b, f768c });
  await t4.close();
  // Phone: Chat tab names its project on Needs me (Atlas, and Harbor after opening a Harbor item); Settings origins
  const p4 = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const pg4 = async (u) => { await p4.goto("file://" + D + "/" + u); await p4.waitForTimeout(120); };
  const tabTxt = () => p4.evaluate(() => document.getElementById("m-chat-tab").textContent.replace(/\s+/g, " ").trim());
  const sheetInfo = () => p4.evaluate(() => ({ page: location.pathname.split("/").pop(), head: document.getElementById("mch-h").textContent, label: document.querySelector('label[for="mci"]').textContent, back: document.getElementById("m-chat-back").textContent.trim() }));
  await pg4("m2-needs-me.html"); const ta4 = await tabTxt();
  await p4.click("#m-chat-tab"); await p4.waitForTimeout(150); const sa4 = await sheetInfo();
  await p4.click("#m-chat-back"); await p4.waitForTimeout(150); const ra4 = await at4(p4);
  ok3("m2: tab reads 'Atlas chat 1 new reply' → Atlas chat, Back to Needs me (static) → m2, focus on the tab", /^Atlas chat 1 new reply$/.test(ta4) && sa4.page === "m10-chat-from-needs.html" && sa4.head === "Atlas chat" && sa4.label === "Message to Atlas chat" && sa4.back === "Back to Needs me" && ra4.page === "m2-needs-me.html" && ra4.focus === "m-chat-tab", { ta4, sa4, ra4 });
  await pg4("m2b-needs-me-harbor.html"); const th4 = await tabTxt();
  await p4.click("#m-chat-tab"); await p4.waitForTimeout(150); const sh2 = await sheetInfo();
  await p4.click("#m-chat-back"); await p4.waitForTimeout(150); const rh4 = await at4(p4);
  ok3("m2b (Harbor item last opened): tab reads 'Harbor chat' → Harbor chat with Harbor composer → back to m2b", /^Harbor chat/.test(th4) && sh2.page === "m10h-chat-harbor.html" && sh2.head === "Harbor chat" && sh2.label === "Message to Harbor chat" && rh4.page === "m2b-needs-me-harbor.html" && rh4.focus === "m-chat-tab", { th4, sh2, rh4 });
  await pg4("m12b-chat-error.html");
  const pfirst = await p4.evaluate(() => [...document.querySelectorAll(".m-acts > *")].map((b) => b.textContent.trim()));
  await p4.click("#restart-agent"); await p4.waitForTimeout(150);
  const s13 = await p4.evaluate(() => ({ page: location.pathname.split("/").pop(), warn: (document.querySelector(".orch-card.warn") || {}).textContent || "", steps: document.querySelectorAll(".restart li").length, back: document.getElementById("m-settings-back").textContent.trim() }));
  await p4.click("#m-settings-back"); await p4.waitForTimeout(150); const r13 = await at4(p4);
  ok3("m12b: restart first4 → m13b amber, 3 steps, Back to the chat → m12b, focus on the restart button", /restart/.test(pfirst[0]) && /Ask again/.test(pfirst[1]) && s13.page === "m13b-settings-not-answering.html" && /Not answering since 14:02/.test(s13.warn) && s13.steps === 3 && s13.back === "Back to the chat" && r13.page === "m12b-chat-error.html" && r13.focus === "restart-agent", { pfirst, s13, r13 });
  await pg4("m2-needs-me.html"); await p4.click("#m-settings"); await p4.waitForTimeout(150);
  const sn4 = await p4.evaluate(() => document.getElementById("m-settings-back").textContent.trim());
  await p4.click("#m-settings-back"); await p4.waitForTimeout(150); const rn4 = await at4(p4);
  ok3("m2 Settings → sheet says Back to Needs me → m2, focus on Settings", sn4 === "Back to Needs me" && rn4.page === "m2-needs-me.html" && rn4.focus === "m-settings", { sn4, rn4 });
  const live4 = await p4.evaluate(() => (document.querySelector('.m-tabs [role="status"]') || {}).textContent);
  ok3("m2: the phone unread count is announced by a status line", live4 === "1 new reply in Atlas chat", { live4 });
  await browser.close();
  console.log(fails ? `${fails} FAILED` : "ALL PASS");
  process.exit(fails ? 1 : 0);
})();
