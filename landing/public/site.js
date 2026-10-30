// Commonplace site: tabs, the agent board, entrances, copy. No dependencies.
(() => {
  const root = document.documentElement;
  root.classList.add("js");
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Reveal a terminal pane line by line, like a session playing back.
  let playing = 0;
  function play(pane) {
    const run = ++playing;
    const lines = [...pane.querySelectorAll(".line")];
    if (still) { lines.forEach((l) => l.classList.add("shown")); return; }
    lines.forEach((l) => l.classList.remove("shown"));
    let t = 0;
    lines.forEach((l) => {
      t += l.classList.contains("hit") ? 520 : l.classList.contains("you") ? 420 : 260;
      setTimeout(() => { if (run === playing) l.classList.add("shown"); }, t);
    });
  }

  function tabs(list, onShow) {
    const tabEls = [...list.querySelectorAll('[role="tab"]')];
    const select = (tab, focus) => {
      tabEls.forEach((t) => {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
      });
      if (focus) tab.focus();
      if (onShow) onShow(document.getElementById(tab.getAttribute("aria-controls")));
    };
    tabEls.forEach((t, i) => {
      t.addEventListener("click", () => select(t));
      t.addEventListener("keydown", (e) => {
        const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
        if (!step) return;
        e.preventDefault();
        select(tabEls[(i + step + tabEls.length) % tabEls.length], true);
      });
    });
  }

  const term = document.querySelector(".term");
  if (term) {
    tabs(term.querySelector('[role="tablist"]'), play);
  }
  document.querySelectorAll('.mods [role="tablist"]').forEach((l) => tabs(l));

  // Agent board: one open row at a time.
  const rows = [...document.querySelectorAll(".row")];
  rows.forEach((row) => row.addEventListener("click", () => {
    const open = row.getAttribute("aria-expanded") !== "true";
    rows.forEach((r) => r.setAttribute("aria-expanded", String(open && r === row)));
  }));

  // Copy the install command.
  document.querySelectorAll("[data-copy]").forEach((btn) => btn.addEventListener("click", async () => {
    const text = document.getElementById(btn.dataset.copy).textContent;
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "Copied";
    } catch {
      getSelection().selectAllChildren(document.getElementById(btn.dataset.copy));
      btn.textContent = "Selected";
    }
    setTimeout(() => { btn.textContent = "Copy"; }, 1600);
  }));

  // Entrances: once, as each part comes into view.
  const targets = document.querySelectorAll(".rise, .shutter");
  if (still || !("IntersectionObserver" in window)) {
    targets.forEach((el) => el.classList.add("in"));
  } else {
    const io = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (!e.isIntersecting) return;
      e.target.classList.add("in");
      io.unobserve(e.target);
    }), { rootMargin: "0px 0px -8% 0px" });
    targets.forEach((el) => io.observe(el));
  }

  if (term) {
    const first = term.querySelector('[role="tabpanel"]:not([hidden])');
    setTimeout(() => play(first), still ? 0 : 500);
  }
})();
