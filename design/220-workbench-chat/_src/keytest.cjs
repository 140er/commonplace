// Live keyboard check of the static screens (playwright-core + local Chrome; no network).
// Usage: node _src/keytest.cjs <path-to-playwright-core>
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
  check("07 Ask a question opens chat", await st(), { open: true, ctx: false, exp: "true", inChat: true });
  await page.click("[data-close-chat]");
  check("07 Back to the approval", await st(), { open: false, ctx: true, exp: "false", focus: "ask-question" });
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
  await browser.close();
  console.log(fails ? `${fails} FAILED` : "ALL PASS");
  process.exit(fails ? 1 : 0);
})();
