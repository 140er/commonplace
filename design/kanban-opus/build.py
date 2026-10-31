"""Writes board.html and card.html from one card list. Run: python3 build.py"""

LOGO = '<svg viewBox="0 0 32 32"><rect width="32" height="32" rx="3" fill="#f7941d"/><rect x="7" y="7" width="8" height="8" fill="#0b0c0b"/><rect x="17" y="7" width="8" height="8" fill="#0b0c0b"/><rect x="7" y="17" width="8" height="8" fill="#0b0c0b"/></svg>'
DRAWER = '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.3"><rect x="1.5" y="2" width="9" height="8" rx="1"/><path d="M4.5 5h3"/></svg>'
CHECK = '<svg class="check" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2 6.5l2.5 2.5L10 3.5"/></svg>'


def side(current):
    boards = [
        ("atlas", "#f7941d", 9),
        ("gallery", "#7fd18b", 3),
        ("workshop", "#9db7ff", 12),
        ("archive", "#c79bf2", 2),
    ]
    cur = ' aria-current="page"'
    rows = "".join(
        f'<a href="board.html"{cur if b == current else ""}><span class="sq" style="background:{c}"></span>{b} <span class="n">{n}</span></a>'
        for b, c, n in boards
    )
    return f"""<aside class="side">
    <div class="ws">{LOGO}sample</div>
    <nav class="nav">
      <a href="decide.html"><svg class="glyph" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2.5 8.5l3.5 3.5 7.5-8"/></svg>Decide <span class="n hot">4</span></a>
      <a href="#"><svg class="glyph" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="12" height="10" rx="1"/><path d="M2 6.5h12"/></svg>Memory</a>
    </nav>
    <hr>
    <nav class="nav">{rows}</nav>
  </aside>"""


def links(n):
    return f'<span class="links">{DRAWER}{n}</span>' if n else ""


TODO = [
    (147, "Canary for board tools", "", 1, ""),
    (148, "Hook line: say when a card is back", "agent:editor", 0, ""),
    (149, "Expiry test: one expire event per stale claim", "", 0, ""),
    (152, "Move rules: one test per API.md row, over MCP and HTTP", "", 0, ""),
    (154, "⌘K: tasks and drawers in one result list", "", 2, ""),
    (155, "Done column shows the last 20", "", 0, ""),
]
DOING = [
    (142, "Fix the Windows lock on repair", "agent:builder", "12m", 60, 3),
    (144, "Race test with real Claude Code and Codex sessions", "sample", "1d", None, 1),
    (146, "board_repos: normalise git remotes", "agent:editor", "27m", 10, 0),
]
REVIEW = [
    (
        145,
        "Pick the token expiry default",
        "agent:editor",
        "2h",
        "1 year or 90 days for personal tokens?",
        2,
    ),
    (
        150,
        "Phone push for the Decide count",
        "agent:editor",
        "1h",
        "A sample notification service, or only the Decide count for v1?",
        2,
    ),
    (151, "Split the hook output", "agent:builder", "40m", None, 0),
]
DONE = [
    (143, "task_take_next in one BEGIN IMMEDIATE", "agent:builder", "3h"),
    (141, "schema.sql: append-only triggers on task_events", "agent:editor", "5h"),
    (140, "Change counter triggers on every table", "agent:editor", "6h"),
    (139, "boards.sqlite3 next to the knowledge graph", "agent:builder", "8h"),
    (138, "Stamp columns filled from the verified token", "agent:editor", "1d"),
    (137, "Read kanzo's claim logic, keep none of its code", "sample", "1d"),
    (136, "Fifth column: review", "sample", "1d"),
    (135, "Race check: 4 processes, 2,001 cards", "agent:builder", "2d"),
    (134, "Claims expire after 30 minutes without a write", "sample", "2d"),
    (133, "Decide queue: one query across boards", "agent:editor", "2d"),
    (132, "Inbox tokens land in backlog only", "agent:builder", "3d"),
    (131, "§9 flags on titles, bodies and asks", "agent:editor", "3d"),
]


def actor(a):
    cls = "actor person" if a == "sample" else "actor"
    return f'<span class="{cls}">{a}</span>'


def cols(focus=None, open_=None):
    def cls(i, extra=""):
        c = "card" + extra
        if i == focus:
            c += " focus"
        if i == open_:
            c += " open"
        return c

    todo = "".join(
        f'<article class="{cls(i)}"><p class="ct">{t}</p><div class="cm"><span class="id">#{i}</span>'
        f'<span class="r">{links(n_links)}{f"<span class=res></span>{actor(a)}" if a else ""}</span></div></article>'
        for i, t, a, n_links, _ in TODO
    )
    doing = ""
    for i, t, a, held, left, n_links in DOING:
        bar = (
            ""
            if left is None
            else f'<span class="claim{" low" if left < 20 else ""}"><i style="width:{left}%"></i></span>'
        )
        doing += (
            f'<article class="{cls(i)}"><p class="ct">{t}</p><div class="cm"><span class="id">#{i}</span>'
            f'<span class="r">{links(n_links)}{actor(a)}<span>{held}</span></span></div>{bar}</article>'
        )
    review = ""
    for i, t, a, age, q, n_links in REVIEW:
        title = (
            f'<span style="font:600 11px var(--mono);color:var(--accent);margin-right:6px">Done?</span>{t}'
            if q is None
            else t
        )
        quote = f'<p class="cq">“{q}”</p>' if q else ""
        review += (
            f'<article class="{cls(i)}"><p class="ct">{title}</p>{quote}<div class="cm"><span class="id">#{i}</span>'
            f'<span class="r">{links(n_links)}{actor(a)}<span>{age}</span></span></div></article>'
        )
    done = "".join(
        f'<article class="card"><p class="ct">{t}</p><div class="cm">{CHECK}<span class="id">#{i}</span>'
        f'<span class="r">{actor(a)}<span>{age}</span></span></div></article>'
        for i, t, a, age in DONE
    )
    return f"""<div class="cols">
      <section class="col rail"><span class="n">9</span><span class="flagged">⚠</span><span class="v">backlog</span></section>
      <section class="col"><header>todo <span class="n">{len(TODO)}</span><span class="add">+</span></header><div class="stack">{todo}</div></section>
      <section class="col"><header>doing <span class="n">{len(DOING)}</span></header><div class="stack">{doing}</div></section>
      <section class="col review"><header>review <span class="n">{len(REVIEW)}</span></header><div class="stack">{review}</div></section>
      <section class="col done"><header>done <span class="n">41</span></header><div class="stack">{done}</div></section>
    </div>"""


HEAD = """<header class="head">
      <h1>atlas</h1>
      <div class="tools">
        <span class="live">live</span>
        <div class="search"><svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/></svg><kbd>⌘K</kbd></div>
        <button class="btn primary">New <kbd>c</kbd></button>
      </div>
    </header>"""

PANEL = f"""<aside class="panel">
      <div class="bar">
        <span class="id">#142</span><span class="faint">/</span><span>atlas</span>
        <span class="nav-k"><kbd>k</kbd><kbd>j</kbd><kbd>esc</kbd></span>
      </div>
      <div class="scroll">
        <div style="display:flex;flex-direction:column;gap:12px">
          <span><span class="status doing">doing</span></span>
          <h2>Fix the Windows lock on repair</h2>
        </div>

        <div class="facts">
          <div><svg class="glyph" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="6" r="3"/><path d="M2.5 14c.8-2.6 3-4 5.5-4s4.7 1.4 5.5 4"/></svg><span class="actor">agent:builder</span></div>
          <div><svg class="glyph" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.5 1.5"/></svg><span class="mono">until 14:30Z</span><span style="margin-left:auto;width:64px;height:3px;background:var(--line-2);border-radius:2px;overflow:hidden"><i style="display:block;width:60%;height:100%;background:var(--ok);opacity:.7"></i></span></div>
        </div>

        <p class="body">On Windows, <code>atlas repair</code> fails with <code>PermissionError: [WinError 32]</code> when ChromaDB still holds <code>chroma.sqlite3</code>. Close the client before the swap, and retry the rename three times with backoff.</p>

        <ul class="drawers">
          <li>{DRAWER}<span class="t">ChromaDB keeps the sqlite handle open until the client is garbage collected</span><span class="d">drawer_2be1</span></li>
          <li>{DRAWER}<span class="t">Windows coverage gate is lower because file-lock cleanup leaves lines unexecuted</span><span class="d">drawer_c044</span></li>
          <li class="gone">{DRAWER}<span class="t">Repair works on Windows if run as administrator</span><span class="d">drawer_5d17</span></li>
        </ul>

        <ol class="log">
          <li class="ev"><span class="dot"></span><span class="line"><span class="actor person">sample</span>added to <b>todo</b></span><time>11:20Z</time></li>
          <li class="ev"><span class="dot"></span><span class="line"><span class="actor">agent:editor</span>claimed</span><time>13:12Z</time></li>
          <li class="ev k-expire"><span class="dot"></span><span class="line"><span class="actor">agent:editor</span>lost the claim</span><time>13:42Z</time></li>
          <li class="ev"><span class="dot"></span><span class="line"><span class="actor">agent:builder</span>claimed</span><time>13:48Z</time></li>
          <li class="ev k-comment"><span class="dot"></span><span class="line"><span class="actor">agent:builder</span></span><time>13:55Z</time>
            <p class="said">Reproduced on Windows: the lock is ours, not Defender. Closing the PersistentClient before os.replace fixes 9 of 10 runs.</p></li>
          <li class="ev k-comment"><span class="dot"></span><span class="line"><span class="actor">agent:builder</span></span><time>14:00Z</time>
            <p class="said">Retry with backoff covers the 10th. Running the full suite on Windows now.</p></li>
        </ol>

        <div class="composer">
          <label class="reply focus">Check the coverage gate on Windows too<span class="caret"></span><kbd>↵</kbd></label>
        </div>
      </div>
    </aside>"""


def page(title, body_cls, inner):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Commonplace</title>
<link rel="stylesheet" href="app.css">
<link rel="stylesheet" href="board.css">
</head>
<body class="{body_cls}">
<div class="app">
  {side("atlas")}
  <main class="main">
    {inner}
  </main>
</div>
</body>
</html>
"""


open("board.html", "w").write(page("atlas", "", HEAD + cols(focus=147)))
open("card.html", "w").write(
    page("#142 Fix the Windows lock on repair", "with-panel", HEAD + cols(open_=142) + PANEL)
)
