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
  await browser.close();
  console.log(fails ? `${fails} FAILED` : "ALL PASS");
  process.exit(fails ? 1 : 0);
})();
