"""Writes decide.html, board.html, card.html and mobile.html from one data set. Run: python3 build.py"""


# ---------- icons ----------
def svg(body, vb=14, cls="", style=""):
    c = f' class="{cls}"' if cls else ""
    s = f' style="{style}"' if style else ""
    return f'<svg{c}{s} viewBox="0 0 {vb} {vb}" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'


MARK = '<svg viewBox="0 0 32 32"><rect x="4" y="4" width="11" height="11" fill="#0b0c0b"/><rect x="17" y="4" width="11" height="11" fill="#0b0c0b"/><rect x="4" y="17" width="11" height="11" fill="#0b0c0b"/></svg>'
I_DRAWER = svg('<rect x="2" y="2.5" width="10" height="9" rx="1.5"/><path d="M5 6h4"/>')
I_FLAG = '<span class="flag">!</span>'


def claim(left):
    """Claim left as six blocks: full at claim, empty at expiry."""
    on = max(1, round(left * 6))
    blocks = "".join('<i class="on"></i>' if k < on else "<i></i>" for k in range(6))
    return f'<span class="claim{" low" if left < 0.2 else ""}">{blocks}</span>'


def links(n):
    return f'<span class="links">{I_DRAWER}{n}</span>' if n else ""


def actor(a, untrusted=False):
    if a == "jeffrey":
        return '<span class="actor person">jeffrey</span>'
    return f'<span class="actor{" untrusted" if untrusted else ""}">{a}</span>'


# ---------- data (DESIGN.md / API.md examples) ----------
DECIDE = [
    dict(
        id=145,
        board="mempalace",
        title="Pick the token expiry default",
        by="claude-code:vm",
        age="2h",
        links=2,
        q="1 year or 90 days for personal tokens?",
        opts=["1y", "90d"],
    ),
    dict(
        id=150,
        board="mempalace",
        title="Phone push for the Decide count",
        by="claude-code:vm",
        age="1h",
        links=0,
        q="Self-hosted ntfy on zurvur, or only the Decide count for v1?",
        opts=["ntfy", "count only"],
    ),
    dict(
        id=151,
        board="mempalace",
        title="Split the hook output",
        by="codex:vm",
        age="40m",
        links=0,
        done=True,
        q=None,
        opts=["ship", "redo"],
    ),
]
FILED = [
    dict(
        id=153,
        board="home",
        title="Add Grok calendar sync",
        by="grok:cos",
        age="1d",
        links=0,
        flag=True,
        q=None,
        opts=["todo", "close"],
    )
]

TODO = [
    (147, "Canary for board tools", "", 1),
    (148, "Hook line: say when a card is back", "claude-code:vm", 0),
    (149, "Expiry test: one expire event per stale claim", "", 0),
    (152, "Move rules: one test per API.md row, over MCP and HTTP", "", 0),
    (154, "⌘K: tasks and drawers in one result list", "", 2),
    (155, "Done column shows the last 20", "", 0),
]
DOING = [
    (142, "Fix the Windows lock on repair", "codex:vm", "12m", 0.6, 3),
    (144, "Race test with real Claude Code and Codex sessions", "jeffrey", "1d", None, 1),
    (146, "board_repos: normalise git remotes", "claude-code:vm", "27m", 0.1, 0),
]
REVIEW = DECIDE
DONE = [
    (143, "task_take_next in one BEGIN IMMEDIATE", "3h"),
    (141, "schema.sql: append-only triggers on task_events", "5h"),
    (140, "Change counter triggers on every table", "6h"),
    (139, "boards.sqlite3 next to the knowledge graph", "8h"),
    (138, "Stamp columns filled from the verified token", "1d"),
    (137, "Read kanzo's claim logic, keep none of its code", "1d"),
    (136, "Fifth column: review", "1d"),
    (135, "Race check: 4 processes, 2,001 cards", "2d"),
    (134, "Claims expire after 30 minutes without a write", "2d"),
    (133, "Decide queue: one query across boards", "2d"),
    (132, "Inbox tokens land in backlog only", "3d"),
    (131, "§9 flags on titles, bodies and asks", "3d"),
    (130, "Board scopes follow memory wings", "4d"),
    (129, "Drawer links both ways", "4d"),
    (128, "Kanban design doc", "5d"),
]


# ---------- pieces ----------
def side(current):
    boards = ["mempalace", "commonplace-site", "computercraft", "home"]
    b = "".join(
        f'<a href="board.html"{" aria-current" if x == current else ""}>{x}</a>' for x in boards
    )
    dec = " aria-current" if current == "decide" else ""
    return f"""<aside class="side">
    <div class="ws"><span class="mark">{MARK}</span><span class="name">jeffrey</span></div>
    <nav class="navs">
      <a href="decide.html"{dec}>Decide<span class="n">4</span></a>
      <a href="#">Search<kbd>⌘K</kbd></a>
      <a href="#">Memory</a>
    </nav>
    <nav class="navs fill">{b}</nav>
  </aside>"""


def item(d, sel=False, mobile=False, reply=None, picked=None):
    pre = '<span class="pre">Done?</span>' if d.get("done") else ""
    flag = I_FLAG if d.get("flag") else ""
    q = f'<blockquote class="q">{d["q"]}</blockquote>' if d["q"] else ""
    opts = ""
    for n, o in enumerate(d["opts"], 1):
        k = f'<span class="k">{n}</span>' if sel and not mobile else ""
        opts += f'<button class="opt{" go" if o == picked else ""}">{o}{k}</button>'
    field = ""
    if reply is not None:
        field = f'<label class="field">{reply}<span class="cursor"></span>{"" if mobile else "<kbd>↵</kbd>"}</label>'
    cls = "item" + (" sel" if sel else "") + (" filed" if d.get("flag") else "")
    if mobile:
        meta = f'<div class="meta"><span>{d["board"]} #{d["id"]}</span>{actor(d["by"], d.get("flag"))}{flag}<span class="push">{d["age"]}</span></div>'
        return f'<article class="{cls}"><div class="top">{meta}<h3>{pre}{d["title"]}</h3></div>{q}<div class="opts">{opts}</div>{field}</article>'
    meta = f'<div class="meta">{links(d["links"])}<span>{d["board"]}</span>{actor(d["by"], d.get("flag"))}<span>{d["age"]}</span></div>'
    top = f'<div class="top"><span class="id">#{d["id"]}</span><h3>{pre}{d["title"]}{flag}</h3>{meta}</div>'
    return f'<article class="{cls}">{top}{q}<div class="opts">{opts}{field}</div></article>'


def queue(narrow=False):
    rows = item(
        DECIDE[0], sel=True, reply="Go with 90 days, keep 1 year as an option.", picked="90d"
    )
    rows += "".join(item(d) for d in DECIDE[1:])
    rows += (
        '<div class="split"></div>' + "".join(item(d) for d in FILED) + '<div class="rest"></div>'
    )
    return f'<section class="queue{" narrow" if narrow else ""}">{rows}</section>'


def board_cols(focus=None, open_=None):
    def cls(i):
        return "card" + (" focus" if i == focus else "") + (" open" if i == open_ else "")

    todo = "".join(
        f'<article class="{cls(i)}"><p class="ct">{t}</p><div class="meta"><span>#{i}</span>'
        f'<span class="push">{links(n_links)}{actor(a) if a else ""}</span></div></article>'
        for i, t, a, n_links in TODO
    )
    doing = ""
    for i, t, a, held, left, n_links in DOING:
        c = claim(left) if left is not None else ""
        doing += (
            f'<article class="{cls(i)}"><p class="ct">{t}</p><div class="meta"><span>#{i}</span>'
            f'<span class="push">{links(n_links)}{actor(a)}<span>{held}</span>{c}</span></div></article>'
        )
    review = ""
    for d in REVIEW:
        pre = '<span class="pre">Done? </span>' if d.get("done") else ""
        q = f'<p class="cq">{d["q"]}</p>' if d["q"] else ""
        review += (
            f'<article class="{cls(d["id"])}"><p class="ct">{pre}{d["title"]}</p>{q}<div class="meta"><span>#{d["id"]}</span>'
            f'<span class="push">{links(d["links"])}{actor(d["by"])}<span>{d["age"]}</span></span></div></article>'
        )
    done = "".join(f"<li><i></i><span>{t}</span><time>{age}</time></li>" for i, t, age in DONE)
    return f"""<div class="cols">
      <section class="col rail"><header><span class="n">9</span></header><div class="stack"><span class="v">backlog</span>{I_FLAG}</div></section>
      <section class="col"><header>todo<span class="n">{len(TODO)}</span></header><div class="stack">{todo}</div></section>
      <section class="col"><header>doing<span class="n">{len(DOING)}</span></header><div class="stack">{doing}</div></section>
      <section class="col review"><header>review<span class="n">{len(REVIEW)}</span></header><div class="stack">{review}</div></section>
      <section class="col done"><header>done<span class="n">41</span></header><ul class="done-list">{done}</ul></section>
    </div>"""


def bar(i, board):
    return (
        f'<div class="bar"><span class="id">#{i}</span><span>{board}</span>'
        f'<span class="right"><kbd>k</kbd><kbd>j</kbd><kbd>esc</kbd></span></div>'
    )


def ev(who, what, time, kind="", said=None):
    s = f'<p class="said">{said}</p>' if said else ""
    return f'<li class="ev {kind}"><time>{time}</time><span>{actor(who)} <span class="v">{what}</span></span>{s}</li>'


PANEL_145 = f"""<aside class="panel">
      <div class="tile top">
        {bar(145, "mempalace")}
        <span><span class="state review">review</span></span>
        <h2>Pick the token expiry default</h2>
        <div class="hit">
          <p class="q">1 year or 90 days for personal tokens?</p>
          <p class="stamp">claude-code:vm · trusted · 2h ago</p>
          <div class="opts"><button class="opt">1y<span class="k">1</span></button><button class="opt go">90d<span class="k">2</span></button></div>
          <label class="field">Go with 90 days, keep 1 year as an option.<kbd>↵</kbd></label>
          <div class="then"><span class="on">claude-code:vm</span><span>anyone</span><span>done</span><span>backlog</span></div>
        </div>
        <p class="prose">PRODUCT-DESIGN §6 says 1 year. Security review asked for 90 days.</p>
        <ul class="drawers">
          <li>{I_DRAWER}<span>Token lifecycle notes: personal tokens rotate on login, bots keep theirs until revoked</span></li>
          <li class="gone">{I_DRAWER}<span>Tokens never expire unless revoked by the owner</span></li>
        </ul>
      </div>
      <ol class="log">
        {ev("claude-code:vm", "add", "12:48")}
        {ev("claude-code:vm", "claim", "13:02")}
        {ev("claude-code:vm", "comment", "13:40", said="Both paths written, tests green on each.")}
        {ev("claude-code:vm", "ask → jeffrey", "13:41", "hand")}
      </ol>
      <div class="composer"><label class="field"><kbd>c</kbd></label><span class="send">↵</span></div>
    </aside>"""

PANEL_142 = f"""<aside class="panel">
      <div class="tile top">
        {bar(142, "mempalace")}
        <span><span class="state doing">doing</span></span>
        <h2>Fix the Windows lock on repair</h2>
        <div class="props"><span>{actor("codex:vm")}</span><span>{claim(0.6)}until 14:30Z</span></div>
        <p class="prose">On Windows, <code>mempalace repair</code> fails with <code>PermissionError: [WinError 32]</code> when ChromaDB still holds <code>chroma.sqlite3</code>. Close the client before the swap, and retry the rename three times with backoff.</p>
        <ul class="drawers">
          <li>{I_DRAWER}<span>ChromaDB keeps the sqlite handle open until the client is garbage collected</span></li>
          <li>{I_DRAWER}<span>Windows coverage gate is lower because file-lock cleanup leaves lines unexecuted</span></li>
          <li class="gone">{I_DRAWER}<span>Repair works on Windows if run as administrator</span></li>
        </ul>
      </div>
      <ol class="log">
        {ev("jeffrey", "add", "11:20")}
        {ev("claude-code:vm", "claim", "13:12")}
        {ev("claude-code:vm", "expire", "13:42", "warn")}
        {ev("codex:vm", "claim", "13:48")}
        {ev("codex:vm", "comment", "13:55", said="Reproduced on t3-win: the lock is ours, not Defender. Closing the PersistentClient before os.replace fixes 9 of 10 runs.")}
        {ev("codex:vm", "comment", "14:00", said="Retry with backoff covers the 10th. Running the full suite on win now.")}
      </ol>
      <div class="composer"><label class="field">Check the coverage gate on Windows too<span class="cursor"></span></label><span class="send go">↵</span></div>
    </aside>"""


def page(title, side_current, main, main_cls=""):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Commonplace</title>
<link rel="stylesheet" href="app.css">
<link rel="stylesheet" href="screens.css">
</head>
<body>
<div class="app">
  {side(side_current)}
  <main class="main{(" " + main_cls) if main_cls else ""}">
    {main}
  </main>
</div>
</body>
</html>
"""


BOARD_HEAD = '<header class="head"><h1>mempalace</h1><div class="right"><button class="blockbtn">New<kbd>c</kbd></button></div></header>'
DECIDE_HEAD = '<header class="head"><h1>Decide <span class="n">4</span></h1></header>'

open("decide.html", "w").write(
    page("Decide", "decide", DECIDE_HEAD + queue(narrow=True) + PANEL_145)
)
open("board.html", "w").write(page("mempalace", "mempalace", BOARD_HEAD + board_cols(focus=147)))
open("card.html", "w").write(
    page("#142", "mempalace", BOARD_HEAD + board_cols(open_=142) + PANEL_142, "dim")
)

mitems = item(DECIDE[0], sel=True, mobile=True, reply="Keep 1 year as an option", picked="90d")
mitems += (
    "".join(item(d, mobile=True) for d in DECIDE[1:])
    + '<div class="split"></div>'
    + "".join(item(d, mobile=True) for d in FILED)
)
open("mobile.html", "w").write(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Decide · Commonplace</title>
<link rel="stylesheet" href="app.css">
<link rel="stylesheet" href="screens.css">
</head>
<body>
<div class="m">
  <header class="mhead"><h1>Decide<span class="n">4</span></h1><span class="mark">{MARK}</span></header>
  <div class="list">{mitems}</div>
  <nav class="tabs"><a aria-current>Decide <b>4</b></a><a>Boards</a><a>Memory</a></nav>
</div>
</body>
</html>
""")
