// Layout self-check of every screen at 1440, 1366, 1280, 1024, 768 and 390 px (phone screens at 390 and 320 px).
// Reports: text clipped by its own box or by a clipping ancestor, a chat log whose last message ends under the
// composer, and a board/Needs me/Memory header that wraps onto a second row at 1024 px and up.
// Usage: node _src/layoutcheck.cjs <path-to-playwright-core>   (uses /usr/bin/google-chrome; no network)
const { chromium } = require(process.argv[2]);
const fs = require("fs");
const path = require("path");
const D = path.resolve(__dirname, "..", "screens");
const SIZES = [[1440, 900], [1366, 768], [1280, 800], [1024, 768], [768, 1024], [390, 844]];

(async () => {
  const browser = await chromium.launch({ executablePath: "/usr/bin/google-chrome", headless: true });
  const page = await browser.newPage();
  const files = fs.readdirSync(D).filter((f) => f.endsWith(".html")).sort();
  let issues = 0, checked = 0;
  for (const f of files) {
    const phone = /^m\d/.test(f);
    for (const [w, h] of phone ? [[390, 844], [320, 640]] : SIZES) {
      await page.setViewportSize({ width: w, height: h });
      await page.goto("file://" + path.join(D, f));
      await page.waitForTimeout(60);
      // Desktop-only screens: their phone form is an m-screen (m13 Settings) or is done on a computer (connect,
      // disconnect, keyboard shortcuts). They are still checked at every desktop and tablet width.
      const desktopOnly = /^(03c|03c2|03d|03e|10|10b|10c|10d|10e|10f|11|11b|12)-/.test(f);
      const found = await page.evaluate(([w, desktopOnly]) => {
        const out = [];
        const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && !el.closest("[hidden], .sr, [inert] .sr"); };
        const name = (el) => (el.id ? "#" + el.id : el.className && typeof el.className === "string" ? el.tagName.toLowerCase() + "." + el.className.split(" ")[0] : el.tagName.toLowerCase()) + " “" + (el.textContent || "").trim().slice(0, 30) + "”";
        // 1. Clipped text: short labels (kbd, tags, chips, buttons, tabs) wider than a clipping ancestor or their own box.
        for (const el of document.querySelectorAll("kbd, .tag, .ct-chip, .btn, .chip, [role=tab], .cid, .chat-toggle, .badge, .where, h1, h2, h3")) {
          if (!vis(el)) continue;
          const cs = getComputedStyle(el);
          if (cs.textOverflow === "ellipsis") continue;
          if (el.scrollWidth > el.clientWidth + 1 && cs.overflowX !== "visible") { out.push("clipped in own box: " + name(el)); continue; }
          let a = el.parentElement;
          while (a && a !== document.body) {
            const acs = getComputedStyle(a);
            if (acs.overflowX !== "visible" && acs.overflowX !== "auto" && acs.overflowX !== "scroll") {
              const r = el.getBoundingClientRect(), ar = a.getBoundingClientRect();
              if (r.right > ar.right + 1 || r.left < ar.left - 1) out.push("clipped by " + (a.id || a.className.split(" ")[0]) + ": " + name(el));
              break;
            }
            if (acs.overflowX === "auto" || acs.overflowX === "scroll") break; // scrolls, not clipped
            a = a.parentElement;
          }
        }
        // 2. Chat: the last message must end above the composer once the log is scrolled to the end.
        for (const [logSel, inSel] of [["#chat:not([hidden]) [role=log]", "#chat:not([hidden]) .ctx-foot"], [".m-chat .m-chat-log", ".m-chat .m-chat-in"]]) {
          const log = document.querySelector(logSel), comp = document.querySelector(inSel);
          if (!log || !comp || !vis(log) || !vis(comp)) continue;
          log.scrollTop = log.scrollHeight;
          const items = [...log.querySelectorAll(".msg, .callout, .suggest, button")].filter(vis);
          const last = items[items.length - 1];
          if (last && last.getBoundingClientRect().bottom > comp.getBoundingClientRect().top + 1) out.push("last chat item runs under the composer");
        }
        // 2b. Reflow at phone widths: nothing a user reads or presses runs off the right edge of the screen.
        if (w <= 390 && !desktopOnly) for (const el of document.querySelectorAll("button, a, [role=tab], h1, h2, h3, p, .tag, .badge")) {
          if (!vis(el) || el.closest(".sr")) continue;
          if (el.getBoundingClientRect().right > innerWidth + 1) out.push("runs off the screen at " + w + " px: " + name(el));
        }
        // 3. Headers at 1024 px and up: the title, the chat control and the primary action share the first row.
        //    Below 1400 px the filter group has its own second row by design; from 1400 px up it shares the first.
        if (w >= 1024) for (const bar of document.querySelectorAll(".center > .bar, main > .bar")) {
          if (!vis(bar)) continue;
          const top = bar.querySelector("h1").getBoundingClientRect().top;
          for (const el of bar.querySelectorAll("#chat-toggle, #add-card, a.btn.ghost"))
            if (vis(el) && Math.abs(el.getBoundingClientRect().top - top) > 14) out.push("header: " + name(el) + " drops below the title row");
          // Needs me and Memory: the filter group stays on the title row too (their description may wrap).
          for (const el of bar.querySelectorAll(":scope > .seg"))
            if (w >= 1400 && vis(el) && Math.abs(el.getBoundingClientRect().top - top) > 14) out.push("header: filters drop below the title row at 1400 px and up");
        }
        return out;
      }, [w, desktopOnly]);
      checked++;
      for (const x of [...new Set(found)]) { issues++; console.log(`ISSUE ${f} @${w}: ${x}`); }
    }
  }
  console.log(`${checked} screen×width renders checked, ${issues} issues`);
  await browser.close();
  process.exit(issues ? 1 : 0);
})();
