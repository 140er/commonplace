"""Generate the static Workbench screens (screens/*.html), index.html and the
screenshot manifest (_src/shots.txt) from shared parts.

Design artefact only; the output HTML is self-contained apart from wb.css and
the copied Geist Mono font. Run: python3 _src/build.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "screens"

P = {
    "search": '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5 14 14"/>',
    "inbox": '<path d="M2 9h3.5l1 2h3l1-2H14"/><path d="M3.8 3h8.4L14 9v4H2V9z"/>',
    "memory": '<path d="M3.5 2.5h6.5L12.5 5v8.5h-9z"/><path d="M5.8 7h4.4M5.8 9.5h4.4M5.8 12h2.4"/>',
    "board": '<rect x="2" y="2.5" width="3.4" height="11" rx=".6"/><rect x="6.3" y="2.5" width="3.4" height="7.5" rx=".6"/><rect x="10.6" y="2.5" width="3.4" height="9" rx=".6"/>',
    "chat": '<path d="M2.5 3.5h11v7H7.2L4.3 13v-2.5H2.5z"/>',
    "settings": '<path d="M2 4.5h7M12.5 4.5H14M2 11.5h1.5M6.5 11.5H14"/><circle cx="10.8" cy="4.5" r="1.6"/><circle cx="5" cy="11.5" r="1.6"/>',
    "plus": '<path d="M8 3v10M3 8h10"/>',
    "check": '<path d="m3 8.5 3 3 7-7"/>',
    "x": '<path d="m4 4 8 8M12 4l-8 8"/>',
    "block": '<circle cx="8" cy="8" r="5.5"/><path d="m4.2 11.8 7.6-7.6"/>',
    "you": '<circle cx="8" cy="8" r="5.5"/><path d="M8 5v3.4M8 10.8v.1"/>',
    "chev": '<path d="m4.5 6 3.5 3.5L11.5 6"/>',
    "arrow": '<path d="M3 8h10M9 4l4 4-4 4"/>',
    "back": '<path d="M13 8H3M7 4 3 8l4 4"/>',
    "undo": '<path d="M5 6.5h5a3 3 0 0 1 0 6H7"/><path d="M7 3.5 4 6.5l3 3"/>',
    "link": '<path d="M7 9a2.5 2.5 0 0 0 3.5 0l2-2A2.5 2.5 0 0 0 9 3.5l-.5.5"/><path d="M9 7a2.5 2.5 0 0 0-3.5 0l-2 2A2.5 2.5 0 0 0 7 12.5l.5-.5"/>',
    "copy": '<rect x="5.5" y="5.5" width="8" height="8" rx="1"/><path d="M10.5 5.5v-3h-8v8h3"/>',
    "agent": '<rect x="3" y="5" width="10" height="7.5" rx="2"/><path d="M8 2.5V5M6 8.5v.6M10 8.5v.6"/>',
    "send": '<path d="M2.5 8 13.5 3l-3 10.5-2.3-4.3z"/><path d="M8.2 9.2 13.5 3"/>',
    "panel": '<rect x="2" y="2.5" width="12" height="11" rx="1.5"/><path d="M10 2.5v11"/>',
    "more": '<circle cx="4" cy="8" r=".9"/><circle cx="8" cy="8" r=".9"/><circle cx="12" cy="8" r=".9"/>',
    "card": '<rect x="2.5" y="3" width="11" height="10" rx="1.5"/><path d="M5 6.5h6M5 9.5h3.5"/>',
    "project": '<path d="M2.5 4.5h4l1.2 1.5h5.8v6.5h-11z"/>',
    "keyboard": '<rect x="1.5" y="4" width="13" height="8" rx="1.5"/><path d="M4 7h.1M6.5 7h.1M9 7h.1M11.5 7h.1M5 9.5h6"/>',
    "mail": '<rect x="2" y="3.5" width="12" height="9" rx="1.5"/><path d="m2.5 4.5 5.5 4 5.5-4"/>',
    "clock": '<circle cx="8" cy="8" r="5.5"/><path d="M8 5v3.2l2 1.3"/>',
    "edit": '<path d="M3 13h2.5L13 5.5 10.5 3 3 10.5z"/><path d="m9 4.5 2.5 2.5"/>',
    "bell": '<path d="M4 11V7.5a4 4 0 0 1 8 0V11l1 1.5H3z"/><path d="M6.8 14h2.4"/>',
    "stop": '<rect x="4" y="4" width="8" height="8" rx="1.2"/>',
    "retry": '<path d="M13 8a5 5 0 1 1-1.5-3.6"/><path d="M13 2.5v3h-3"/>',
}


def ic(name, cls="i"):
    return (
        f'<svg class="{cls}" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
        f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{P[name]}</svg>'
    )


def page(title, body, mobile=False):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Commonplace (Workbench)</title>
<link rel="stylesheet" href="wb.css">
<script src="wb.js" defer></script>
</head>
<body{' class="mobile"' if mobile else ""}>
{body}
</body>
</html>
"""


# ---------------------------------------------------------------- data

PROJECTS = [
    ("atlas", "Atlas", "Mobile app beta", "#2E43C0", 2, "3 agents working"),
    ("harbor", "Harbor", "Website relaunch", "#B4561A", 1, "1 agent working"),
    ("ledger", "Ledger", "Q4 bookkeeping", "#3F6E5A", 0, "Idle"),
    ("field", "Field notes", "Customer research", "#7A5BC2", 0, "Idle"),
]
ATLAS_ONLY = [("atlas", "Atlas", "", "#2E43C0", 0, "New project")]
PEOPLE = {"Maya": "#3F6E5A", "Leon": "#9A4A1E"}

# The orchestrator's request, used word for word on desktop and mobile.
APPROVAL_QUOTE = (
    "Beta 0.9 is ready. A-118, A-134 and A-135 are done: the sign-up email arrives on all 3 test "
    "domains, and Leon reviewed the release notes.\n\n"
    "If you approve, I'll send build 0.9 (212 of 212 tests passed) to the 40 people in the Beta group "
    "and post the release notes. If you decline, nothing is sent."
)


def av(letter, agent=False, lg=False, color=None):
    cls = "av" + (" agent" if agent else "") + (" lg" if lg else "")
    style = f' style="background:{color}"' if color and not agent else ""
    return f'<span class="{cls}"{style} aria-hidden="true">{letter}</span>'


def who(name, agent=False):
    if agent:
        return f'<span class="who agent">{av(name[0], True)}<b>{name}</b><span class="sr">(agent)</span></span>'
    return f'<span class="who">{av(name[0], color=PEOPLE.get(name))}{name}</span>'


def rail(nav=None, current="atlas", needs=3, projects=None):
    projects = PROJECTS if projects is None else projects
    nav_needs = ' aria-current="page"' if nav == "needs" else ""
    nav_mem = ' aria-current="page"' if nav == "memory" else ""
    count = (
        f'<span class="count need" aria-label="{needs} items">{needs}</span>'
        if needs
        else '<span class="count quiet" aria-label="no items">0</span>'
    )
    items = []
    for key, name, _desc, color, n, act in projects:
        cur = ' aria-current="page"' if (key == current and nav is None) else ""
        right = f'<span class="count need" aria-label="{n} need you">{n}</span>' if n else ""
        items.append(
            f'<a class="proj" href="#"{cur}><span class="sw" style="background:{color}" aria-hidden="true"></span>'
            f'<span class="pn"><b>{name}</b><small>{act}</small></span>{right}</a>'
        )
    return f"""<nav class="rail" aria-label="Workspace">
  <button class="ws" aria-label="Workspace: Northwind. Switch workspace"><span class="mark" aria-hidden="true">N</span>Northwind {ic("chev", "i chev")}</button>
  <button class="cmdk" id="cmdk" aria-keyshortcuts="Control+K Meta+K">{ic("search")}<span>Search or do…</span><kbd>⌘/Ctrl K</kbd></button>
  <a class="nav" href="#"{nav_needs}>{ic("inbox")}Needs me {count}</a>
  <a class="nav" href="#"{nav_mem}>{ic("memory")}Memory</a>
  <div class="rail-h"><span id="ph">Projects</span><button class="iconbtn" id="new-project" aria-label="New project">{ic("plus")}</button></div>
  <div role="list" aria-labelledby="ph">{"".join(f'<div role="listitem">{x}</div>' for x in items)}</div>
  <div class="rail-foot">
    <a class="nav" href="#" id="shortcuts-link">{ic("keyboard")}Shortcuts <kbd style="margin-left:auto">?</kbd></a>
    <div class="me">{av("M", color=PEOPLE["Maya"])}Maya</div>
  </div>
</nav>"""


CHIPS = {
    "new": '<span class="ct-chip new">1 new<span class="sr"> reply</span></span>',
    "none": '<span class="ct-chip off">no orchestrator yet</span>',
    "": "",
}


def chat_toggle(open_=False, chip=""):
    """The one control that opens and closes the project chat (B + chat). Status shows only by exception."""
    return (
        f'<button class="chat-toggle" id="chat-toggle" aria-controls="chat" aria-expanded="{"true" if open_ else "false"}" '
        f'aria-keyshortcuts="/">{ic("chat")}Chat{CHIPS[chip]}<kbd>/</kbd></button>'
    )


def board_bar(orch=True, name="Atlas", sub="Mobile app beta", blocked=1, chat=False, chip=None):
    if chip is None:
        chip = "" if orch else "none"
    o = chat_toggle(chat, chip)
    filt = (
        f"""<div class="seg" role="group" aria-label="Filter cards"><button aria-pressed="true">All</button><button aria-pressed="false">Mine</button><button aria-pressed="false">Agents</button><button aria-pressed="false">Blocked {blocked}</button></div>"""
        if blocked is not None
        else ""
    )
    return f"""<header class="bar">
  <h1 title="{sub}">{name}</h1>{o}
  <span class="spacer"></span>
  {filt}
  <a class="btn ghost" href="#">{ic("settings")}Settings</a>
  <button class="btn only-narrow" id="details-btn" aria-controls="ctx" aria-expanded="false">{ic("panel")}Details</button>
  <button class="btn primary" id="add-card" aria-keyshortcuts="C">{ic("plus")}Add card <kbd>C</kbd></button>
</header>"""


def card(cid, title, meta, tag="", cls="", note="", state="", more=False, dep=False):
    """state: words added to the accessible name (Blocked, Needs you, New)."""
    selected = "is-selected" in cls
    name = f"{cid} {title}" + (f", {state}" if state else "")
    morebtn = (
        f'<button class="iconbtn" id="more-{cid}" aria-label="Actions for {cid}: move, edit">{ic("more")}</button>'
        if more
        else ""
    )
    notehtml = ""
    if note:
        notehtml = (
            f'<p class="card-note dep">{ic("link")}{note}</p>'
            if dep
            else f'<p class="card-note">{note}</p>'
        )
    return f"""<article class="card {cls}" tabindex="{0 if selected else -1}" aria-label="{name}"{' aria-current="true"' if selected else ""}>
  <div class="card-top"><span class="cid">{cid}</span>{tag}{morebtn}</div>
  <h3>{title}</h3>
  <div class="card-meta">{meta}</div>{notehtml}
</article>"""


OPEN = '<span class="open-claim">Available for an agent</span>'
T_BLOCK = f'<span class="tag block">{ic("block")}Blocked</span>'
T_YOU = f'<span class="tag you">{ic("you")}Needs me</span>'
T_NEW = '<span class="tag new">New · from chat</span>'


def lbl(t):
    return f'<span class="tag lbl">{t}</span>'


def col(name, n, cards_html, hint="", add=True, more=0):
    # Roving tabindex: one tab stop per column (the selected card, else the first).
    if 'tabindex="0"' not in cards_html:
        cards_html = cards_html.replace('tabindex="-1"', 'tabindex="0"', 1)
    addb = (
        f'<button class="iconbtn" aria-label="Add card to {name}">{ic("plus")}</button>'
        if add
        else ""
    )
    return f"""<section class="col" aria-label="{name}, {n} cards">
  <div class="col-h"><h2>{name}</h2><span class="n">{n}</span>{addb}</div>{f'<p class="col-hint">{hint}</p>' if hint else ""}
  <div class="cards">{cards_html}</div>{f'<a class="more" href="#">Show {more} more</a>' if more else ""}
</section>"""


DONE = f"""<section class="col" aria-label="Done, 14 cards">
  <div class="col-h"><h2>Done</h2><span class="n">14</span></div>
  <ul class="done-list">
    <li>{ic("check")}<a class="done-link" href="#" tabindex="0"><span class="cid">A-112</span> Beta sign-up form</a></li>
    <li>{ic("check")}<a class="done-link" href="#" tabindex="-1"><span class="cid">A-110</span> Crash reporting wired up</a></li>
    <li>{ic("check")}<a class="done-link" href="#" tabindex="-1"><span class="cid">A-109</span> Store listing text</a></li>
    <li>{ic("check")}<a class="done-link" href="#" tabindex="-1"><span class="cid">A-107</span> App icon, 3 variants</a></li>
  </ul>
  <a class="more" href="#">Show 10 more</a>
</section>"""


def c118(more=False):
    return card(
        "A-118",
        "Fix sign-up email not arriving on some domains",
        f"{who('Iris', True)}<span>· working 2 h</span>",
        lbl("bug"),
        more=more,
    )


def c121(sel=True, more=False):
    return card(
        "A-121",
        "Offline mode: queue edits while disconnected",
        f"{who('Otto', True)}<span>· 3 h</span>",
        T_BLOCK,
        "is-blocked" + (" is-selected" if sel else ""),
        "Waiting on Leon: which rate limit applies to batch sync?",
        state="Blocked",
        more=more,
    )


def c124(more=False):
    return card(
        "A-124",
        "Translate the settings screen (de, nl, fr)",
        f"{who('Juno', True)}<span>· 40 min</span>",
        more=more,
    )


C_115 = card(
    "A-115",
    "Push-notification opt-in screen",
    f"Built by {who('Iris', True)}",
    T_YOU,
    "is-you",
    state="Needs me",
)
C_116 = card("A-116", "Cold start under 2 seconds", f"Reviewer {who('Leon')}")
C_131 = card("A-131", "Write onboarding copy for the invite screen", OPEN, lbl("copy"))
C_129 = card("A-129", "Add dark-mode screenshots to the store listing", who("Leon"), lbl("store"))
C_132 = card("A-132", "Collect crash logs from beta group 1", OPEN)
C_126 = card("A-126", "Choose the app icon variant for the beta", who("Maya"))


def board(todo, doing=None, review=None):
    doing = doing if doing is not None else c118() + c121() + c124()
    review = review if review is not None else C_115 + C_116
    return f"""<div class="board" role="region" aria-label="Board">
{todo}
{col("Doing", 3, doing)}
{col("Review", 2, review)}
{DONE}
</div>"""


def ctx(tab, body, foot=""):
    tabs = []
    for name, icn in (("Details", "card"), ("Memory", "memory")):
        sel = name == tab
        tabs.append(
            f'<button role="tab" id="tab-{name.lower()}" aria-selected="{"true" if sel else "false"}" '
            f'aria-controls="ctx-panel" tabindex="{0 if sel else -1}">{ic(icn)}{name}</button>'
        )
    return f"""<aside class="context" id="ctx" aria-label="Context panel">
  <div class="ctx-head"><div class="ctx-tabs" role="tablist" aria-label="Context">{"".join(tabs)}</div><button class="hide" aria-keyshortcuts="]">{ic("panel")}Hide</button></div>
  <div class="ctx-panel-wrap" id="ctx-panel" role="tabpanel" aria-labelledby="tab-{tab.lower()}" style="display:flex;flex-direction:column;flex:1;min-height:0">
  {body}
  {f'<div class="ctx-foot">{foot}</div>' if foot else ""}
  </div>
</aside>"""


def shell(
    center,
    context=None,
    nav=None,
    extra="",
    needs=3,
    projects=None,
    current="atlas",
    inert=False,
    panel_open=False,
    chat=None,
    chat_open=False,
):
    """chat: the chat pane. It shares the right-hand slot with the context panel; only one shows at a time.
    Board screens get the default chat pane (hidden while closed) so the toggle works live."""
    if chat is None and 'id="chat-toggle"' in center:
        chat = CHAT_DEFAULT
    cls = (
        ("shell" if context or chat_open else "shell two")
        + (" panel-open" if panel_open else "")
        + (" chat-open" if chat_open else "")
    )
    bg = " inert" if panel_open else ""
    r = rail(nav, current, needs, projects).replace(
        '<nav class="rail"', f'<nav class="rail"{bg}', 1
    )
    if context and chat_open:
        context = context.replace(
            '<aside class="context" id="ctx"', '<aside class="context" id="ctx" hidden', 1
        )
    if chat and not chat_open:
        chat = chat.replace('<aside class="chatpane"', '<aside class="chatpane" hidden', 1)
    return f"""<a class="skip" href="#main">Skip to main content</a>
<div class="{cls}"{" inert" if inert else ""}>
{r}
<main class="center" id="main"{bg}>
{center}
</main>
{'<div class="tablet-scrim" aria-hidden="true"></div>' if panel_open else ""}
{context or ""}
{chat or ""}
</div>
{extra}"""


def chat_input(idn="ci", disabled=False, value="", focus=False, attach=""):
    return f"""<div class="chat-input">{attach}<label class="sr" for="{idn}">Message to Atlas chat</label><div class="box{" is-focus" if focus else ""}"><textarea id="{idn}" rows="2" placeholder="Ask for something…"{" autofocus" if focus else ""}>{value}</textarea><button class="btn primary sm" aria-label="Send">{ic("send")}</button></div><div class="hint"><span>Enter to send · Shift+Enter new line</span><span><kbd>Esc</kbd> closes the chat</span></div></div>"""


def chatpane(body, foot=None, connected=True, about="", ret="chat-toggle"):
    """The project chat. A complementary region, never modal on desktop or tablet, so the board stays usable."""
    sub = "Orchestrator connected" if connected else "No orchestrator yet"
    foot = chat_input("ci") if foot is None else foot
    return f"""<aside class="chatpane" id="chat" aria-labelledby="chat-h" data-return-focus="{ret}">
  <div class="chat-head"><span class="ch-ic" aria-hidden="true">{ic("chat")}</span><div class="ch-t"><h2 id="chat-h">Atlas chat</h2><p>{sub}</p></div><button class="btn ghost sm" id="chat-close" aria-keyshortcuts="]">{ic("x")}Close chat</button></div>
  {about}
  <div class="ctx-body" role="log" aria-label="Messages in Atlas chat">
  {body}
  </div>
  <div class="ctx-foot">{foot}</div>
</aside>"""


def sel(label, val):
    return f'<button class="sel" aria-label="{label}: {val}. Change">{val}{ic("chev", "i chev")}</button>'


def dialog(title, body, acts, wide=False, alert=False, ret=""):
    role = "alertdialog" if alert else "dialog"
    return f"""<div class="scrim" aria-hidden="true"></div>
<div class="dialog{" wide" if wide else ""}" role="{role}" aria-modal="true" aria-labelledby="dlg-h"{f' data-return-focus="{ret}"' if ret else ""}>
  <h2 id="dlg-h">{title}</h2>
  {body}
  <div class="acts">{acts}</div>
</div>"""


# ---------------------------------------------------------------- screens

screens = {}
TERMS = """<dl class="terms" aria-label="Two words you will see">
      <div><dt>Agent</dt><dd>An AI helper that works on cards, for example a coding assistant on your computer.</dd></div>
      <div><dt>Orchestrator</dt><dd>The project's lead agent. It reads what you ask in chat and turns it into cards. Each project gets one automatically: the first agent you connect.</dd></div>
    </dl>"""


def auth(left, right=None):
    right = (
        right
        or f"""<section class="auth-r" aria-labelledby="next-h">
    <h2 id="next-h" style="font-size:15px;color:var(--text-2);font-weight:650">What happens next</h2>
    <ol class="steps3">
      <li><div><b>Name your first project</b><span>A project is one board, one chat and its memory.</span></div></li>
      <li><div><b>Add a card or ask in chat</b><span>Cards are the work. Agents pick them up.</span></div></li>
      <li><div><b>Connect an agent</b><span>We show you how, step by step.</span></div></li>
    </ol>
    {TERMS}
  </section>"""
    )
    return f"""<main class="auth" id="main">
  <div class="auth-l">
    <div class="brand"><span class="mark" aria-hidden="true">C</span>Commonplace</div>
    {left}
  </div>
  {right}
</main>"""


# 01 Sign up --------------------------------------------------------------
screens["01-signup"] = page(
    "Sign up",
    auth(
        f"""<div style="display:flex;flex-direction:column;gap:10px">
      <h1>Create your workspace</h1>
      <p class="lead">One place where you and your AI agents share a board, a chat and a memory that keeps your exact words.</p>
    </div>
    <form>
      <label class="lbl">Your name<input class="field" value="Maya" autocomplete="name"></label>
      <label class="lbl">Email<input class="field" type="email" value="maya@example.com" autocomplete="email"></label>
      <button class="btn primary lg" type="button" style="justify-content:center">{ic("mail")}Email me a sign-in link</button>
    </form>
    <p class="fine">No password. Your data stays on the machine that runs Commonplace. Already have a workspace? <a href="#">Sign in</a></p>"""
    ),
)

screens["01b-check-email"] = page(
    "Check your email",
    auth(
        f"""<div style="display:flex;flex-direction:column;gap:10px">
      <h1 tabindex="-1">Check your email</h1>
      <p class="lead" role="status">We sent a sign-in link to <b>maya@example.com</b>. Open it on this device. It works once, for 15 minutes.</p>
    </div>
    <div class="facts"><span><b>Not there?</b> Look in spam or promotions. It can take a minute.</span></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn lg">{ic("retry")}Send the link again</button><button class="btn ghost lg">Use a different email</button></div>
    <p class="fine">You can close this tab. The link opens Commonplace and signs you in.</p>"""
    ),
)

screens["01c-link-expired"] = page(
    "Sign-in link expired",
    auth(
        f"""<div style="display:flex;flex-direction:column;gap:10px">
      <div class="alert" role="alert"><h3>{ic("clock")}This sign-in link has expired</h3><span style="color:var(--text)">Links work once and for 15 minutes. Nothing is lost; get a new one below.</span></div>
      <h1>Get a new link</h1>
    </div>
    <form>
      <label class="lbl">Email<input class="field" type="email" value="maya@example.com" autocomplete="email"></label>
      <button class="btn primary lg" type="button" style="justify-content:center">{ic("mail")}Email me a new link</button>
    </form>
    <p class="fine">Still stuck? The person who runs Commonplace for you can check that email sending is set up.</p>"""
    ),
)

# 02 Create project ---------------------------------------------------------
empty_board_cols = f"""<div class="board" role="region" aria-label="Board" style="opacity:.45">
{col("Todo", 0, "", add=False)}{col("Doing", 0, "", add=False)}{col("Review", 0, "", add=False)}{col("Done", 0, "", add=False)}
</div>"""
screens["02-create-project"] = page(
    "Create your first project",
    shell(
        f"""<header class="bar"><h1>Welcome, Maya</h1></header>{empty_board_cols}""",
        nav="none",
        needs=0,
        projects=[],
        inert=True,
        extra=dialog(
            "Create your first project",
            f"""<p class="lead" style="margin-top:-8px">A project is one board, one chat and one memory. You can make more later.</p>
  <label class="lbl">Project name<input class="field is-focus" value="Atlas" autofocus style="outline-offset:0"></label>
  <label class="lbl">What is it for? <span class="help">Optional. Agents read this to understand the work.</span><textarea class="field" rows="2">Mobile app beta for 40 testers, due end of November.</textarea></label>
  <div class="callout info"><h3>{ic("agent")}No agent yet? That's fine.</h3><span>You can add cards and write in chat right away. When you connect your first agent, it becomes this project's orchestrator (its lead agent) automatically.</span></div>""",
            """<button class="btn ghost">Cancel <kbd>Esc</kbd></button><button class="btn primary">Create project <kbd>↵</kbd></button>""",
            ret="new-project",
        ),
    ),
)


# 03 Empty board -------------------------------------------------------------
def firststeps(done1=False, done2=False, now=1):
    def step(n, title, text, btn, done, cur):
        cls = "fs now" if cur else "fs"
        num = ic("check") if done else str(n)
        numcls = "num" + (" ok" if done else "")
        style = ' style="background:var(--ok);color:#fff"' if done else ""
        return f'<div class="{cls}" role="listitem"><span class="{numcls}"{style} aria-hidden="true">{num}</span><div><b>{title}{"<span class=sr> (done)</span>" if done else ""}</b><p>{text}</p>{btn}</div></div>'

    return f"""<div class="firststeps" role="list" aria-label="First steps">
  {step(1, "Add your first card", "A card is one piece of work. Write it like a to-do.", f'<button class="btn {"primary " if now == 1 else ""}sm">{ic("plus")}Add card <kbd>C</kbd></button>', done1, now == 1)}
  {step(2, "Connect an agent", "An agent is an AI helper that works on cards. The first one becomes the orchestrator.", f'<button class="btn {"primary " if now == 2 else ""}sm" id="connect-btn">{ic("agent")}Connect an agent</button>', done2, now == 2)}
  {step(3, "Ask for something", "Write in chat. The orchestrator turns requests into cards.", f'<button class="btn sm" aria-controls="chat">{ic("chat")}Write in chat <kbd>/</kbd></button>', False, now == 3)}
</div>"""


eboard = f"""<div class="board" role="region" aria-label="Board">
{col("Todo", 0, f'<button class="add-ghost">{ic("plus")}Add card</button>', "Work waiting to be picked up.")}
{col("Doing", 0, '<p class="empty-col">Nothing yet</p>', "An agent or a person is on it.", add=False)}
{col("Review", 0, '<p class="empty-col">Nothing yet</p>', "Done, waiting for a check.", add=False)}
{col("Done", 0, '<p class="empty-col">Nothing yet</p>', "Finished and checked.", add=False)}
</div>"""
NO_CARD = ctx(
    "Details",
    '<div class="ctx-body"><p style="color:var(--text-2)">Select a card to see its details here.</p></div>',
)
empty_chat = chatpane(
    f"""<div class="callout info"><h3>{ic("chat")}This is Atlas chat. You can already write here.</h3><span>Ask for work in your own words. The <b>orchestrator</b>, the project's lead agent, reads it and turns it into cards. There is no orchestrator until you connect your first agent, so for now your messages wait here and you can turn any of them into a card yourself.</span>
    <div class="acts"><button class="btn sm" id="connect-btn-chat">{ic("agent")}Connect an agent</button></div></div>
  <div><p class="sec-h" style="margin-bottom:8px">Try asking</p>
  <div class="suggest"><button class="chip">Plan the beta launch</button><button class="chip">Split this into cards: …</button><button class="chip">What is blocked?</button></div></div>""",
    connected=False,
)
screens["03-board-empty"] = page(
    "Atlas board (empty)",
    shell(
        board_bar(orch=False, blocked=None, chat=True) + firststeps() + eboard,
        NO_CARD,
        needs=0,
        projects=ATLAS_ONLY,
        chat=empty_chat,
        chat_open=True,
    ),
)

# 03b Chat with no agent: held message + failed send -------------------------------
waiting_chat = chatpane(
    f"""<div class="msgs">
    <p class="daysep">Today</p>
    <div class="msg">{av("M", lg=True, color=PEOPLE["Maya"])}<div><div class="msg-h"><b>Maya</b><time>10:02</time></div>
      <div class="msg-b"><p>Plan the beta launch for 40 testers by the end of November.</p>
      <div class="msg-state" role="status"><span style="display:flex;gap:6px;align-items:center"><span class="spin" aria-hidden="true"></span><b>Waiting for an agent.</b></span><span>No agent is connected, so nobody has read this yet. It stays here and goes to the orchestrator as soon as one connects.</span>
      <div class="acts"><button class="btn sm primary">{ic("agent")}Connect an agent</button><button class="btn sm">{ic("card")}Make it a card now</button></div></div></div></div></div>
    <div class="msg">{av("M", lg=True, color=PEOPLE["Maya"])}<div><div class="msg-h"><b>Maya</b><time>10:04</time></div>
      <div class="msg-b"><p>Also add a card for the store listing.</p>
      <div class="msg-state err" role="alert"><b>Not sent. This device is offline.</b><span style="color:var(--text)">Your words are kept here until it sends.</span><div class="acts"><button class="btn sm">{ic("retry")}Try again</button><button class="btn sm ghost">{ic("edit")}Edit</button></div></div></div></div></div>
  </div>""",
    connected=False,
)
screens["03b-chat-no-agent"] = page(
    "Chat before an agent is connected",
    shell(
        board_bar(orch=False, blocked=None, chat=True) + firststeps(now=2) + eboard,
        NO_CARD,
        needs=0,
        projects=ATLAS_ONLY,
        chat=waiting_chat,
        chat_open=True,
    ),
)

# 03c-e Connect an agent ---------------------------------------------------------------
connect_intro = """<p class="lead" style="margin-top:-8px">An agent is an AI helper that runs on your computer or a server, for example a coding assistant. It connects to Commonplace through MCP, an open standard many agent apps support.</p>
  <div class="facts"><span><b>You need:</b> an agent app that supports MCP, and access to its settings. About 2 minutes.</span></div>"""

WHERE_TO_PASTE = """<ol class="howto" aria-label="Where to paste it">
        <li>1. In your agent app, open its settings and find <b>MCP servers</b> (some apps call it <b>Tools</b> or <b>Connectors</b>).</li>
        <li>2. Choose <b>Add server</b>, then <b>Paste configuration</b> (or open its config file and paste at the end of the server list).</li>
        <li>3. Save, then restart the app.</li>
      </ol>"""


def connect_steps(state):
    name_done = state != "start"
    s1 = (
        f'<li><span class="n ok" aria-hidden="true">{ic("check")}</span><div><b>Name this connection</b><span class="d">“Maya\'s laptop”. You see this name in Settings.</span></div></li>'
        if name_done
        else '<li><span class="n now" aria-hidden="true">1</span><div><label class="lbl" for="cn">Name this connection <span class="help">So you can tell your agents apart in Settings.</span><input class="field is-focus" id="cn" value="Maya\'s laptop" autofocus></label></div></li>'
    )
    if state == "start":
        s2 = f"""<li><span class="n" aria-hidden="true">2</span><div><b>Copy the setup and paste it into your agent app</b>
      <span class="d">The setup is a short block of text. It looks like this; the real key is inside the copied text and is never shown on screen:</span>
      <div class="setup-preview" aria-label="Example of the setup, key hidden">name: commonplace-atlas
address: &lt;your Commonplace address&gt;/mcp
key: ••••••••  (in the copied text only)</div>
      <div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button class="btn primary">{ic("copy")}Create and copy setup</button><button class="btn ghost">Can't copy? Show the setup to select it</button></div>
      {WHERE_TO_PASTE}</div></li>"""
    else:
        s2 = f"""<li><span class="n ok" aria-hidden="true">{ic("check")}</span><div><b>Copy the setup into your agent app</b><span class="d">Paste it where the app lists its MCP servers, then restart the app.</span>
      <div class="snippet"><span>Setup copied. You can copy the same setup again; the key stays hidden.</span><button class="btn sm"{" autofocus" if state == "wait" else ""}{" " if False else ""}>{ic("copy")}Copy same setup again</button></div></div></li>"""
    s3 = {
        "start": '<li><span class="n" aria-hidden="true">3</span><div><b>Start or restart your agent app</b><span class="d">This dialog shows when it connects.</span></div></li>',
        "wait": '<li><span class="n now" aria-hidden="true">3</span><div><b>Start or restart your agent app</b><span class="d" role="status" style="display:flex;gap:8px;align-items:center;margin-top:4px"><span class="spin" aria-hidden="true"></span>Waiting for your agent to connect… Keep this open.</span></div></li>',
        "fail": '<li><span class="n now" aria-hidden="true" style="background:var(--block)">3</span><div><b>Start or restart your agent app</b></div></li>',
    }[state]
    return f'<ol class="dsteps">{s1}{s2}{s3}</ol>'


delivered_chat = chatpane(
    f"""<div class="msgs">
    <p class="daysep">Today</p>
    <div class="msg">{av("M", lg=True, color=PEOPLE["Maya"])}<div><div class="msg-h"><b>Maya</b><time>10:02</time></div>
      <div class="msg-b"><p>Plan the beta launch for 40 testers by the end of November.</p>
      <div class="msg-state" role="status"><span style="display:flex;gap:6px;align-items:center">{ic("check")}<b>Delivered to the orchestrator at 10:11.</b></span><span>It answers here.</span></div></div></div></div>
  </div>""",
)


def connect_shell(dlg, done=False):
    return shell(
        board_bar(orch=done, blocked=None, chat=True)
        + firststeps(done1=True, done2=done, now=3 if done else 2)
        + eboard,
        NO_CARD,
        needs=0,
        projects=ATLAS_ONLY,
        inert=True,
        extra=dlg,
        chat=delivered_chat if done else empty_chat,
        chat_open=True,
    )


screens["03c-connect-agent"] = page(
    "Connect an agent",
    connect_shell(
        dialog(
            "Connect an agent to Atlas",
            connect_intro + connect_steps("start"),
            '<button class="btn">Cancel <kbd>Esc</kbd></button>',
            wide=True,
            ret="connect-btn",
        )
    ),
)
screens["03c2-connect-waiting"] = page(
    "Connect an agent: waiting",
    connect_shell(
        dialog(
            "Connect an agent to Atlas",
            connect_steps("wait")
            + WHERE_TO_PASTE.replace('class="howto"', 'class="howto" style="margin:0"'),
            '<button class="btn">Cancel <kbd>Esc</kbd></button>',
            wide=True,
            ret="connect-btn",
        )
    ).replace('<button class="btn sm" autofocus>', '<button class="btn sm is-focus" autofocus>'),
)
screens["03d-connect-done"] = page(
    "Agent connected",
    connect_shell(
        dialog(
            "Your agent on “Maya's laptop” is now Atlas's orchestrator",
            f"""<div class="callout ok" role="status"><h3>{ic("check")}Connected at 10:11</h3><span style="color:var(--text)">It is the first agent in Atlas, so it became the project's <b>orchestrator</b>: it reads chat and turns requests into cards. You talk to it in <b>Chat</b>, next to the project name above the board. Your waiting message from 10:02 has been delivered to it.</span></div>
  <p class="lead">More agents can join later. They take cards; the orchestrator stays the same.</p>""",
            f'<button class="btn">{ic("plus")}Connect another agent</button><button class="btn primary is-focus" autofocus>Go to the board</button>',
            wide=True,
            ret="connect-btn",
        ),
        done=True,
    ),
)
screens["03e-connect-failed"] = page(
    "Agent did not connect",
    connect_shell(
        dialog(
            "Connect an agent to Atlas",
            connect_steps("fail")
            + f"""<div class="alert" role="alert"><h3>{ic("you")}No agent has connected in 10 minutes</h3>
  <ul style="margin:0;padding-left:18px;color:var(--text);display:flex;flex-direction:column;gap:4px">
    <li>Is the agent app running? Restart it after pasting the setup.</li>
    <li>Did you paste the whole setup into its MCP server list?</li>
    <li>Does the app run on a computer that can reach this Commonplace?</li>
  </ul></div>""",
            f'<button class="btn">Make a new setup</button><button class="btn primary is-focus" autofocus>{ic("retry")}Keep waiting</button>',
            wide=True,
            ret="connect-btn",
        )
    ),
)


# 04 Board -------------------------------------------------------------------
def card_detail_body(title_html):
    return f"""<div class="ctx-body">
  <div class="detail-h">
    <div class="row"><span class="cid">A-121</span>{T_BLOCK}<span style="margin-left:auto;display:flex;gap:2px"><button class="iconbtn" aria-label="Copy link to card">{ic("link")}</button><button class="iconbtn" aria-label="More actions for A-121">{ic("more")}</button></span></div>
    {title_html}
  </div>
  <dl class="fields">
    <dt>Status</dt><dd>{sel("Status", "Doing")}</dd>
    <dt>Working on it</dt><dd>{who("Otto", True)}<span style="color:var(--text-3);font-size:12.5px">since 11:02</span></dd>
    <dt>Waiting on</dt><dd>{who("Leon")}</dd>
    <dt>Labels</dt><dd>{lbl("sync")} {lbl("beta")}</dd>
    <dt>Due</dt><dd>{sel("Due", "Fri 6 Nov")}</dd>
  </dl>
  <div class="callout block" role="note">
    <h3>{ic("block")}Blocked since 12:40</h3>
    <p class="quote" style="border-color:var(--block-line)">“I need to know which rate limit applies to batch sync before I can size the queue. @Leon, is it the per-user or the per-workspace limit?”</p>
    <span style="color:var(--text-2);font-size:12.5px">Otto (agent), exact words. Leon has this in his Needs me.</span>
    <div class="acts"><button class="btn sm">{ic("memory")}Search memory for the answer</button><button class="btn sm">{ic("bell")}Remind Leon</button></div>
  </div>
  <div><div class="sec-row"><p class="sec-h">Description</p><button class="btn ghost sm">{ic("edit")}Edit</button></div>
  <div class="body-text"><p>Edits made offline should be kept on the phone and sent when the connection returns. Conflicts: last write wins, but keep both versions in history.</p></div></div>
  <div><p class="sec-h" style="margin-bottom:8px">Activity</p>
  <ul class="activity">
    <li>{av("O", True)}<div><span class="verb"><b>Otto</b> marked this blocked</span><div class="meta">12:40</div></div></li>
    <li>{av("O", True)}<div><span class="verb"><b>Otto</b> started working on it</span><div class="meta">11:02</div></div></li>
    <li>{av("M", color=PEOPLE["Maya"])}<div><span class="verb"><b>Maya</b> created the card</span><div class="meta">Tue 3 Nov</div></div></li>
  </ul></div>
</div>"""


TITLE_VIEW = f'<div class="title-row"><h2>Offline mode: queue edits while disconnected</h2><button class="btn ghost sm" aria-keyshortcuts="E">{ic("edit")}Edit <kbd>E</kbd></button></div>'
COMMENT = """<div class="reply"><label class="sr" for="cm">Comment on A-121</label><textarea class="field" id="cm" rows="1" placeholder="Comment or @mention…"></textarea><button class="btn">Send</button></div>"""
card_detail = ctx("Details", card_detail_body(TITLE_VIEW), COMMENT)
todo04 = col("Todo", 4, C_131 + C_129 + C_132 + C_126)


# Chat history shared by the chat screens. Maya asked at 12:30; the 12:41 reply is the "1 new" on the closed toggle.
def ref_row(cid, title, where, tag, linked=False, focus=False):
    cls = ' class="is-focus" data-autofocus' if focus else ""
    return (
        f'<a href="#" data-card="{cid}"{cls}>{tag}<span><span class="cid">{cid}</span> {title}</span>'
        f'<span class="where">{where}</span><span class="sr">, show on the board</span></a>'
    )


def hist(new_sep=False):
    sep = '<p class="newsep" role="separator">New since you last looked</p>' if new_sep else ""
    return f"""<p class="daysep">Today</p>
    <div class="msg">{av("M", lg=True, color=PEOPLE["Maya"])}<div><div class="msg-h"><b>Maya</b><time>12:30</time></div>
      <div class="msg-b"><p>Is anything stuck on Atlas?</p></div></div></div>
    {sep}
    <div class="msg">{av("A", True, lg=True)}<div><div class="msg-h"><b>Atlas orchestrator</b><span class="tag agent">agent</span><time>12:41</time></div>
      <div class="msg-b"><p>One thing. A-121 is blocked: Otto needs to know which rate limit applies to batch sync. I asked Leon, so it's in his Needs me, not yours. Everything else is moving.</p>
      <div class="made" aria-label="Cards in this answer"><div class="made-h"><span>Cards in this answer</span><span>select to show on the board</span></div>
        {ref_row("A-121", "Offline mode: queue edits…", "Doing · Otto", T_BLOCK)}</div></div></div></div>"""


MAYA_ASK = f"""<div class="msg">{av("M", lg=True, color=PEOPLE["Maya"])}<div><div class="msg-h"><b>Maya</b><time>14:02</time></div>
      <div class="msg-b"><p>Before Friday's beta: can you get release notes drafted, and make sure the sign-up email bug is really fixed first?</p></div></div></div>"""
ASK_TEXT = "Before Friday's beta: can you get release notes drafted, and make sure the sign-up email bug is really fixed first?"
CHAT_DEFAULT = chatpane(f'<div class="msgs">{hist(True)}</div>')
screens["04-board"] = page("Atlas board", shell(board_bar(chip="new") + board(todo04), card_detail))

# 05 Add card (composer) ------------------------------------------------------
composer = f"""<form class="composer" aria-label="New card in Todo">
  <label class="sr" for="nt">Card title</label>
  <textarea class="ttl is-focus" id="nt" rows="3" autofocus aria-describedby="nh">Add Android screenshots to the store listing</textarea>
  <label class="vis" for="nd">Details <span style="font-weight:400;color:var(--text-3)">(optional)</span></label>
  <textarea id="nd">Phone and 7-inch tablet, light and dark.</textarea>
  <div class="chips" role="group" aria-label="Card fields">
    <button class="chip" type="button">Who: <b>Any agent</b>{ic("chev", "i")}</button>
    <button class="chip" type="button">Label: <b>store</b>{ic("chev", "i")}</button>
    <button class="chip" type="button">Due: <b>none</b>{ic("chev", "i")}</button>
  </div>
  <div class="composer-foot"><button class="btn primary sm" type="button">Add card <kbd>↵</kbd></button><button class="btn ghost sm" type="button">Cancel</button></div>
  <p class="composer-hint" id="nh">Enter adds · Esc cancels · Tab to fields</p>
</form>"""
todo05 = col("Todo", 4, composer + C_131 + C_129 + C_132, more=1)
screens["05-add-card"] = page(
    "Add a card",
    shell(board_bar(chip="new") + board(todo05, doing=c118() + c121(False) + c124()), card_detail),
)

# 05b Card added + editing in place ----------------------------------------------
c_new = card(
    "A-136",
    "Add Android screenshots to the store listing",
    OPEN,
    lbl("store"),
    "is-selected is-new",
    state="New",
)
new_detail = ctx(
    "Details",
    f"""<div class="ctx-body">
  <div class="detail-h">
    <div class="row"><span class="cid">A-136</span><span class="tag ok">{ic("check")}Added to Todo</span></div>
    <label class="sr" for="ti">Card title</label>
    <textarea class="title-input is-focus" id="ti" rows="2" aria-describedby="tih" autofocus>Add Android screenshots to the store listing</textarea>
    <span class="edit-hint" id="tih">Editing title · Enter saves · Esc cancels</span>
  </div>
  <dl class="fields">
    <dt>Status</dt><dd>{sel("Status", "Todo")}</dd>
    <dt>Who</dt><dd><span class="open-claim">Available for an agent</span></dd>
    <dt>Labels</dt><dd>{lbl("store")}<button class="iconbtn" style="width:24px;height:24px" aria-label="Add label">{ic("plus")}</button></dd>
    <dt>Due</dt><dd>{sel("Due", "Fri 13 Nov")}<span class="saved" role="status">{ic("check")}Saved</span></dd>
  </dl>
  <div><div class="sec-row"><p class="sec-h">Details</p><button class="btn ghost sm">{ic("edit")}Edit</button></div><div class="body-text"><p>Phone and 7-inch tablet, light and dark.</p></div></div>
  <div class="callout info"><h3>{ic("agent")}What happens now</h3><span>A free agent can take this card. Right now 3 of 3 agents are busy, so it waits in Todo. You'll see who took it on the card.</span></div>
</div>""",
)
todo05b = col("Todo", 5, c_new + C_131 + C_129 + C_132, more=1)
toast_add = f"""<div class="toast"><span role="status" class="toast-msg">{ic("check", "i ic")}Card A-136 added to Todo</span><button class="btn sm">{ic("undo")}Undo <kbd>Z</kbd></button></div>"""
screens["05b-card-added"] = page(
    "Card added",
    shell(
        board_bar(chip="new") + board(todo05b, doing=c118() + c121(False) + c124()),
        new_detail,
        extra=toast_add,
    ),
)

# 06 Chat with the orchestrator: open, thinking, answer, error, empty -----------------
T_FROM = '<span class="tag new">From chat · 14:03</span>'
c134 = card(
    "A-134",
    "Draft beta 0.9 release notes",
    OPEN,
    T_FROM,
    "is-new is-linked",
    state="New, from chat 14:03",
)
c135 = card(
    "A-135",
    "Check the sign-up email on 3 test domains",
    OPEN,
    T_FROM,
    "is-new",
    "Starts after A-118 is done",
    state="New, from chat 14:03",
    dep=True,
)
ANSWER = f"""<div class="msg" id="m-1403">{av("A", True, lg=True)}<div><div class="msg-h"><b>Atlas orchestrator</b><span class="tag agent">agent</span><time>14:03</time></div>
      <div class="msg-b"><p>Yes. I made two cards and linked the bug that's already in progress. Release notes wait until the fix is checked.</p>
      <div class="made" aria-label="Cards from this request">
        <div class="made-h"><span>From this request · on the board now</span><span>2 new · 1 linked</span></div>
        {ref_row("A-134", "Draft beta 0.9 release notes", "Todo", '<span class="tag new">New</span>', focus=True)}
        {ref_row("A-135", "Check the sign-up email on 3 test domains", "Todo", '<span class="tag new">New</span>')}
        {ref_row("A-118", "Fix sign-up email not arriving…", "Doing · Iris", '<span class="tag lbl">Linked</span>')}
      </div>
      <p>When all three are done I'll ask you to approve sending beta 0.9 to the testers. That will show up in Needs me.</p></div></div></div>"""
todo06 = col("Todo", 6, c134 + c135 + C_131 + C_129, more=2)
board04_open = board(todo04)
board06 = board(todo06)
todo06_plain = col("Todo", 6, c134.replace(" is-linked", "") + c135 + C_131 + C_129, more=2)


def chat_screen(title, body, board_html=None, foot=None, chip=""):
    pane = chatpane(f'<div class="msgs">{body}</div>', foot)
    return page(
        title,
        shell(
            board_bar(chat=True, chip=chip) + (board_html or board04_open),
            card_detail,
            chat=pane,
            chat_open=True,
        ),
    )


screens["06a-chat-open"] = chat_screen(
    "Chat open", hist(True), foot=chat_input("ci", value=ASK_TEXT, focus=True)
)
screens["06b-chat-thinking"] = chat_screen(
    "Chat: the orchestrator is working on it",
    hist()
    + MAYA_ASK
    + f"""
    <div class="msg">{av("A", True, lg=True)}<div><div class="msg-h"><b>Atlas orchestrator</b><span class="tag agent">agent</span></div>
      <div class="msg-state" role="status"><span style="display:flex;gap:6px;align-items:center"><span class="spin" aria-hidden="true"></span><b>Working on your request…</b></span><span>It read your message at 14:02 and is looking at the board. Answers usually take under a minute. You can keep working; new cards appear on the board when it answers.</span></div></div></div>""",
)
screens["06c-chat-answer"] = chat_screen(
    "Chat: answer and new cards", hist() + MAYA_ASK + ANSWER, board06
)
screens["06d-chat-error"] = chat_screen(
    "Chat: no answer",
    hist()
    + MAYA_ASK
    + f"""
    <div class="msg">{av("A", True, lg=True)}<div><div class="msg-h"><b>Atlas orchestrator</b><span class="tag agent">agent</span></div>
      <div class="msg-state err" role="alert"><b>No answer yet.</b><span style="color:var(--text)">It read your message at 14:02 but hasn't answered in 5 minutes. Its agent on “Maya's laptop” was last active at 14:03; the app may be closed or the computer asleep. Your message is kept and nothing was changed on the board.</span>
      <div class="acts"><button class="btn sm primary">{ic("retry")}Ask again</button><button class="btn sm">{ic("card")}Make it a card now</button><a class="btn sm ghost" href="#">Check the agent in Settings</a></div></div></div></div>""",
)
screens["06e-chat-empty"] = page(
    "Chat: nothing asked yet",
    shell(
        board_bar(chat=True) + board04_open,
        card_detail,
        chat_open=True,
        chat=chatpane(
            f"""<div class="callout info"><h3>{ic("chat")}Ask the orchestrator for something</h3><span>Write what you need in your own words. The Atlas orchestrator, the project's lead agent, reads the board, answers here and turns the work into cards. You see them appear on the board, marked “From chat”. Anything that leaves this workspace, like an email to testers, still waits for you in Needs me.</span></div>
  <div><p class="sec-h" style="margin-bottom:8px">Try asking</p>
  <div class="suggest"><button class="chip">What is blocked?</button><button class="chip">Plan the beta launch</button><button class="chip">Split A-121 into smaller cards</button></div></div>""",
            chat_input("ci", focus=True),
        ),
    ),
)


# 07 Needs me ---------------------------------------------------------------------
def needs_head(n_text):
    return f"""<header class="bar"><h1>Needs me</h1><span class="sub">{n_text}</span>
  <span class="spacer"></span>
  <div class="seg" role="group" aria-label="Filter"><button aria-pressed="true">All</button><button aria-pressed="false">Approvals</button><button aria-pressed="false">Decisions</button><button aria-pressed="false">Reviews</button></div></header>"""


def row(typ, typcls, title, sub, right, cls="", current=False):
    cur = ' aria-current="true"' if current else ""
    return f"""<div class="row-item {cls}"><span class="typ {typcls}">{typ}</span><div><h3><a href="#"{cur}>{title}</a></h3><div class="sub">{sub}</div></div>{right}</div>"""


KEYS_NEEDS = """<div class="keys" aria-label="Keyboard shortcuts"><span><kbd>J</kbd><kbd>K</kbd> move</span><span><kbd>↵</kbd> open linked card</span><span><kbd>R</kbd> ask a question</span><span>Approve and Decline have no single-key shortcut, on purpose: use the buttons.</span></div>"""


def needs_list(done=False):
    rows = []
    if done:
        rows.append('<p class="group-h">Approved, not sent yet</p>')
        rows.append(
            row(
                "Waiting",
                "pending",
                "Send beta 0.9 to 40 testers",
                "<span>Atlas · approved by you 14:21 · Commonplace holds it until <b>14:22</b>; nothing has been sent</span>",
                f'<button class="btn sm" aria-keyshortcuts="Z">{ic("stop")}Stop · 0:48</button>',
                "is-pending",
            )
        )
        rows.append('<p class="group-h">Done today</p>')
        rows.append(
            row(
                "Approval",
                "done",
                "Publish the Harbor privacy page",
                "Harbor · approved 09:40 · released to the orchestrator 09:41 · it reported “published” at 09:43 · can't be undone here",
                '<span class="age">Reported done</span>',
                "is-done",
            )
        )
    rows.append('<p class="group-h">Atlas</p>')
    if not done:
        rows.append(
            row(
                "Approval",
                "approve",
                "Send beta 0.9 to 40 testers?",
                f"{who('Atlas orchestrator', True)}<span>· blocks the beta</span>",
                '<span class="age">12 min</span>',
                "is-selected",
                current=True,
            )
        )
    rows.append(
        row(
            "Review",
            "review",
            '<span class="cid">A-115</span> Push-notification opt-in screen',
            f"{who('Iris', True)}<span>· 2 screenshots, 1 question</span>",
            '<span class="age">1 h</span>',
        )
    )
    rows.append('<p class="group-h">Harbor</p>')
    rows.append(
        row(
            "Decision",
            "decide",
            "Pick the homepage headline",
            f"{who('Harbor orchestrator', True)}<span>· 2 options</span>",
            '<span class="age">3 h</span>',
            "is-selected" if done else "",
            current=done,
        )
    )
    head = needs_head(
        ("2 items" if done else "3 items")
        + " across 2 projects. Only what an agent can't do without you."
    )
    return head + f'<div class="list">{"".join(rows)}</div>' + KEYS_NEEDS


def linked_cards(mobile=False):
    h = ' style="min-height:44px"' if mobile else ""
    return f"""<div class="linked">
    <a href="#"{h}>{ic("check")}<span><span class="cid">A-118</span> Fix sign-up email not arriving on some domains</span><span class="tag ok">Done</span></a>
    <a href="#"{h}>{ic("check")}<span><span class="cid">A-134</span> Draft beta 0.9 release notes</span><span class="tag ok">Done</span></a>
    <a href="#"{h}>{ic("check")}<span><span class="cid">A-135</span> Check the sign-up email on 3 test domains</span><span class="tag ok">Done</span></a>
  </div>"""


SEND_FACT = f"""<div class="facts" role="note"><span style="display:flex;gap:6px;align-items:center">{ic("mail")}<b>Goes outside this workspace</b></span><span>40 testers get an email with the build. Commonplace holds your approval for <b>60 seconds</b> before the orchestrator receives it, so it can't send earlier. Until then, Stop cancels it and nothing is sent. After that it can't be undone here.</span></div>"""

CONFIRM = f"""<div class="confirm" id="confirm-send" role="group" aria-labelledby="cf-h"{{hidden}}>
  <h3 id="cf-h">Send beta 0.9 to 40 testers?</h3>
  <p>Your approval reaches the orchestrator at <b>14:22</b>, 60 seconds after you confirm. Until then you can stop it from Needs me.</p>
  <div class="acts"><button class="btn{{cfocus}}" data-autofocus>Cancel</button><button class="btn primary">{ic("check")}Yes, approve</button></div>
</div>"""
APPROVE_BTNS = f"""<div style="display:flex;flex-direction:column;gap:8px"{{bhidden}}><button class="btn primary" style="justify-content:center;min-height:40px" data-reveal="confirm-send">{ic("check")}Approve and send…</button>
<div class="decide-acts"><button class="btn danger">{ic("x")}Decline</button><button class="btn" id="ask-question" aria-keyshortcuts="R" aria-controls="chat" aria-expanded="false">{ic("chat")}Ask a question <kbd>R</kbd></button></div></div>"""
APPROVE_FOOT = APPROVE_BTNS.replace("{bhidden}", "") + CONFIRM.replace(
    "{hidden}", " hidden"
).replace("{cfocus}", "")
CONFIRM_FOOT = APPROVE_BTNS.replace("{bhidden}", " hidden") + CONFIRM.replace(
    "{hidden}", ""
).replace("{cfocus}", " is-focus")

approval = ctx(
    "Details",
    f"""<div class="ctx-body">
  <div class="detail-h">
    <div class="row"><span class="typ approve">Approval</span><span style="color:var(--text-3);font-size:12.5px">Atlas · asked 14:09</span></div>
    <h2 tabindex="-1" data-autofocus class="is-focus" style="outline-offset:4px;border-radius:2px">Send beta 0.9 to 40 testers?</h2>
  </div>
  <div style="display:flex;flex-direction:column;gap:6px">
    <div class="src">{who("Atlas orchestrator", True)}<span class="sep">·</span><span class="verbatim">exact words</span></div>
    <p class="quote">{APPROVAL_QUOTE}</p>
  </div>
  <div><p class="sec-h" style="margin-bottom:6px">Linked cards</p>{linked_cards()}</div>
  {SEND_FACT}
  <label class="lbl">Note to the orchestrator <span class="help">Optional. Sent with your answer.</span><textarea class="field" rows="1" placeholder="e.g. Send at 09:00 tomorrow"></textarea></label>
</div>""",
    APPROVE_FOOT,
)
ASK_PANE = chatpane(
    f"""<div class="msgs">
    <button class="btn ghost sm" style="align-self:center">Show 4 earlier messages</button>
    <div class="msg">{av("M", lg=True, color=PEOPLE["Maya"])}<div><div class="msg-h"><b>Maya</b><time>14:15</time></div>
      <div class="msg-b"><p class="msg-about"><span class="typ approve">Approval</span>Send beta 0.9 to 40 testers?</p><p>Who exactly is in the Beta group? Is anyone outside the company?</p></div></div></div>
    <div class="msg">{av("A", True, lg=True)}<div><div class="msg-h"><b>Atlas orchestrator</b><span class="tag agent">agent</span><time>14:15</time></div>
      <div class="msg-b"><p>The Beta group has 40 people: 38 testers who signed up through the beta form (A-112) and 2 from your team, you and Leon. So 38 are outside the company.</p><p>Nothing has been sent. The approval is still waiting for you in Needs me.</p></div></div></div>
  </div>""",
    chat_input("ci", focus=True),
    about=f"""<div class="chat-about" role="note"><span class="typ approve">Approval</span><span>You are asking about <b>Send beta 0.9 to 40 testers?</b> Approve or decline it in Needs me.</span><button class="btn sm" data-close-chat>{ic("back")}Back to the approval</button></div>""",
    ret="ask-question",
)
screens["07-needs-me"] = page(
    "Needs me", shell(needs_list(), approval, nav="needs", needs=3, chat=ASK_PANE)
)
screens["07d-needs-me-ask"] = page(
    "Needs me: ask the orchestrator a question",
    shell(
        needs_list(),
        approval.replace(' data-autofocus class="is-focus"', ' class=""').replace(
            'aria-controls="chat" aria-expanded="false"',
            'aria-controls="chat" aria-expanded="true"',
        ),
        nav="needs",
        needs=3,
        chat=ASK_PANE,
        chat_open=True,
    ),
)
screens["07c-approve-confirm"] = page(
    "Needs me: confirm an external send",
    shell(
        needs_list(),
        approval.replace(APPROVE_FOOT, CONFIRM_FOOT).replace(
            ' data-autofocus class="is-focus"', ' class=""'
        ),
        nav="needs",
        needs=3,
    ),
)

# 07b Needs me: empty ---------------------------------------------------------------
screens["07b-needs-me-empty"] = page(
    "Needs me (nothing to do)",
    shell(
        needs_head("Nothing waiting.")
        + f"""<div class="list" style="display:flex"><div class="empty" role="status">
  <span class="big" aria-hidden="true">{ic("check")}</span>
  <h2>Nothing needs you right now</h2>
  <p>Agents ask here when they need an approval, a decision or a review. The count next to Needs me tells you when something arrives.</p>
  <div class="acts"><a class="btn" href="#">{ic("board")}Atlas: 3 cards in progress</a><a class="btn" href="#">{ic("board")}Harbor: 1 card in progress</a></div>
</div></div>""",
        nav="needs",
        needs=0,
        projects=[PROJECTS[0][:4] + (0, PROJECTS[0][5]), PROJECTS[1][:4] + (0, PROJECTS[1][5])]
        + PROJECTS[2:],
    ),
)

# 08 Needs me: approved, send window, next item ------------------------------------------
decision = ctx(
    "Details",
    f"""<div class="ctx-body">
  <div class="detail-h">
    <div class="row"><span class="typ decide">Decision</span><span style="color:var(--text-3);font-size:12.5px">Harbor · asked 11:30</span></div>
    <h2 tabindex="-1" data-autofocus>Pick the homepage headline</h2>
  </div>
  <div style="display:flex;flex-direction:column;gap:6px">
    <div class="src">{who("Harbor orchestrator", True)}<span class="sep">·</span><span class="verbatim">exact words</span></div>
    <p class="quote">Both versions passed the readability check. I can't choose between them without you; it's a brand call. The page ships with whichever you pick.</p>
  </div>
  <fieldset style="border:0;padding:0;margin:0;display:flex;flex-direction:column;gap:6px"><legend class="sec-h" style="margin-bottom:6px">Choose one</legend>
    <div class="radios">
      <label class="radio"><input type="radio" name="hl"><span><b>A · “Plan your harbour stay in two minutes”</b><small>Plain, task first.</small></span></label>
      <label class="radio"><input type="radio" name="hl"><span><b>B · “Every berth, one booking”</b><small>Short, brand voice.</small></span></label>
    </div>
  </fieldset>
</div>""",
    f"""<div class="decide-acts"><button class="btn" aria-keyshortcuts="R">{ic("chat")}Ask a question <kbd>R</kbd></button><button class="btn" aria-disabled="true">Pick an option first</button></div>""",
)
toast_ok = f"""<div class="toast" style="left:calc(236px + (100vw - 236px - 396px)/2);bottom:60px"><span role="status" class="toast-msg">{ic("clock", "i ic")}Approved. Reaches the orchestrator at 14:22 (0:48). Nothing sent yet.</span><button class="btn sm">{ic("stop")}Stop <kbd>Z</kbd></button></div>"""
screens["08-needs-me-done"] = page(
    "Needs me: approved",
    shell(
        needs_list(done=True),
        decision,
        nav="needs",
        needs=2,
        extra=toast_ok,
        projects=[PROJECTS[0][:4] + (1, PROJECTS[0][5])] + PROJECTS[1:],
    ),
)


# 09 Memory search -------------------------------------------------------------------
def mem_head(query, status, filters_project="All"):
    return f"""<header class="bar"><h1>Memory</h1><span class="sub">Exact words from chats, cards and notes. Never summarised.</span></header>
<div style="padding:14px 16px 6px;display:flex;flex-direction:column;gap:10px">
  <div class="search-big" role="search">{ic("search")}<label class="sr" for="mq">Search memory</label><input id="mq" type="search" value="{query}" placeholder="Search exact words…"><kbd>↵</kbd></div>
  <div class="filters"><button class="chip">Project: <b>{filters_project}</b>{ic("chev", "i")}</button><button class="chip">Source: <b>Any</b>{ic("chev", "i")}</button><button class="chip">From: <b>Anyone</b>{ic("chev", "i")}</button><button class="chip">When: <b>Any time</b>{ic("chev", "i")}</button><span style="margin-left:auto;font-size:12.5px;color:var(--text-3)" role="status">{status}</span></div>
</div>"""


mem_center = (
    mem_head("rate limit batch sync", "3 results")
    + f"""
<div class="list" style="padding-top:4px">
  <a class="res is-selected" href="#" aria-current="true"><div class="src">{ic("chat")}<b>Atlas chat</b><span class="sep">·</span>{who("Leon")}<span class="sep">·</span><span>Wed 28 Oct 2026, 16:12</span></div>
    <p class="ex">“<mark>Batch sync</mark> should use the per-workspace <mark>limit</mark>, 600 requests a minute. The per-user <mark>limit</mark> is only for the public API.”</p></a>
  <a class="res" href="#"><div class="src">{ic("card")}<b>Card A-121</b><span class="sep">·</span>{who("Otto", True)}<span class="sep">·</span><span>today, 12:40</span></div>
    <p class="ex">“I need to know which <mark>rate limit</mark> applies to <mark>batch sync</mark> before I can size the queue. …”</p></a>
  <a class="res" href="#"><div class="src">{ic("memory")}<b>Note: API design</b><span class="sep">·</span>{who("Maya")}<span class="sep">·</span><span>Mon 12 Oct 2026</span></div>
    <p class="ex">“Keep one <mark>rate limit</mark> per workspace for internal traffic (<mark>sync</mark>, exports). Public API gets per-user limits.”</p></a>
</div>
<div class="keys"><span><kbd>J</kbd><kbd>K</kbd> move</span><span><kbd>↵</kbd> open source</span><span><kbd>L</kbd> link to a card</span><span><kbd>Y</kbd> copy exact words</span></div>"""
)
mem_ctx = ctx(
    "Memory",
    f"""<div class="ctx-body">
  <div class="detail-h"><span class="verbatim">{ic("memory")}Exact words, as saved. Shown with the messages around it.</span>
  <div class="src">{ic("chat")}<b>Atlas chat</b><span class="sep">·</span><span>Wed 28 Oct 2026</span></div></div>
  <div class="thread">
    <div class="msg ctxmsg">{av("O", True, lg=True)}<div><div class="msg-h"><b>Otto</b><span class="tag agent">agent</span><time>16:09</time></div><div class="msg-b">Starting the sync design. Which limits apply to background traffic?</div></div></div>
    <div class="msg hit">{av("L", lg=True, color=PEOPLE["Leon"])}<div><div class="msg-h"><b>Leon</b><time>16:12</time></div><div class="msg-b"><mark>Batch sync</mark> should use the per-workspace <mark>limit</mark>, 600 requests a minute. The per-user <mark>limit</mark> is only for the public API.</div></div></div>
    <div class="msg ctxmsg">{av("O", True, lg=True)}<div><div class="msg-h"><b>Otto</b><span class="tag agent">agent</span><time>16:13</time></div><div class="msg-b">Noted.</div></div></div>
  </div>
  <div class="callout wait"><h3>{ic("link")}This may answer blocked card A-121</h3><span>Post Leon's message, word for word, as a comment on <span class="nowrap">A-121</span>:</span>
    <p class="quote" style="background:#fff;padding:6px 10px;border-radius:4px">Leon in Atlas chat, Wed 28 Oct 2026, 16:12: “Batch sync should use the per-workspace limit, 600 requests a minute. The per-user limit is only for the public API.”</p>
    <span style="font-size:12.5px;color:var(--text-2)">Otto is notified and decides whether it can continue. Leon's own request stays open until he answers or you close it.</span>
    <div class="acts"><button class="btn sm primary">{ic("send")}Post on A-121</button></div></div>
</div>""",
    f"""<div style="display:flex;gap:6px;flex-wrap:wrap"><button class="btn sm" id="open-in-chat" aria-controls="chat" aria-expanded="false">{ic("chat")}Open in chat, ask about it <kbd>↵</kbd></button><button class="btn sm" aria-keyshortcuts="Y">{ic("copy")}Copy exact words <kbd>Y</kbd></button><button class="btn sm" aria-keyshortcuts="L">{ic("link")}Link to card <kbd>L</kbd></button></div>""",
)
MEM_PANE = chatpane(
    f"""<div class="msgs">
    <p class="daysep">Wed 28 Oct 2026</p>
    <div class="msg">{av("O", True, lg=True)}<div><div class="msg-h"><b>Otto</b><span class="tag agent">agent</span><time>16:09</time></div><div class="msg-b">Starting the sync design. Which limits apply to background traffic?</div></div></div>
    <div class="msg hit" id="hit">{av("L", lg=True, color=PEOPLE["Leon"])}<div><div class="msg-h"><b>Leon</b><time>16:12</time><span class="tag lbl">Found in memory</span></div><div class="msg-b">Batch sync should use the per-workspace limit, 600 requests a minute. The per-user limit is only for the public API.</div></div></div>
    <div class="msg">{av("O", True, lg=True)}<div><div class="msg-h"><b>Otto</b><span class="tag agent">agent</span><time>16:13</time></div><div class="msg-b">Noted.</div></div></div>
    <button class="btn sm" style="align-self:center">{ic("arrow")}Jump to today's messages</button>
  </div>""",
    chat_input(
        "ci",
        value="Does this answer Otto's question on A-121?",
        focus=True,
        attach=f'<div class="attach">{ic("memory")}<span>Quoting <b>Leon, Wed 28 Oct 2026, 16:12</b>, exact words</span><button class="iconbtn sm-ic" aria-label="Remove the quote">{ic("x")}</button></div>',
    ),
    about=f"""<div class="chat-about" role="note">{ic("memory")}<span>Opened at Leon's message from memory. Your question takes the quote along, word for word.</span><button class="btn sm" data-close-chat>{ic("back")}Back to the source</button></div>""",
    ret="open-in-chat",
)
screens["09-memory"] = page(
    "Memory search", shell(mem_center, mem_ctx, nav="memory", chat=MEM_PANE)
)
screens["09f-memory-ask"] = page(
    "Memory: ask about a result in chat",
    shell(mem_center, mem_ctx, nav="memory", chat=MEM_PANE, chat_open=True),
)

screens["09e-memory-searching"] = page(
    "Memory: searching",
    shell(
        mem_head(
            "rate limit batch sync exports",
            '<span style="display:inline-flex;gap:6px;align-items:center"><span class="spin" aria-hidden="true"></span>Searching… showing results for “rate limit batch sync” until done</span>',
        )
        + mem_center.split('<div class="list" style="padding-top:4px">', 1)[1]
        .split('<div class="keys">')[0]
        .replace('class="res', 'class="res is-loading')
        .join(
            [
                '<div class="list" style="padding-top:4px" aria-busy="true"><p class="group-h">Results for “rate limit batch sync” · updating</p>',
                "",
            ]
        ),
        None,
        nav="memory",
    ),
)
screens["09b-memory-no-results"] = page(
    "Memory: no results",
    shell(
        mem_head("rate limit for exports", "0 results in Atlas", "Atlas")
        + f"""<div class="list" style="display:flex"><div class="empty" role="status">
  <span class="big neutral" aria-hidden="true">{ic("search")}</span>
  <h2>No exact words match in Atlas</h2>
  <p>Memory finds the words you type, not their meaning. Try fewer or different words, or search every project.</p>
  <div class="acts"><button class="btn primary">Search all projects</button><button class="btn">Clear filters</button></div>
</div></div>""",
        nav="memory",
    ),
)
screens["09c-memory-empty"] = page(
    "Memory: nothing saved yet",
    shell(
        mem_head("", "")
        + f"""<div class="list" style="display:flex"><div class="empty">
  <span class="big neutral" aria-hidden="true">{ic("memory")}</span>
  <h2>Nothing saved yet</h2>
  <p>Chats, cards and notes appear here as exact words as soon as they exist. Nothing is summarised or rewritten.</p>
  <div class="acts"><button class="btn">{ic("chat")}Open chat</button><button class="btn">{ic("plus")}Add a card</button></div>
</div></div>""",
        nav="memory",
        needs=0,
        projects=ATLAS_ONLY,
    ),
)
screens["09d-memory-error"] = page(
    "Memory: search failed",
    shell(
        mem_head("rate limit batch sync", "")
        + f"""<div class="list" style="display:flex"><div class="empty" role="alert">
  <span class="big err" aria-hidden="true">{ic("you")}</span>
  <h2>Search couldn't run</h2>
  <p>Search is updating after a new version. This takes about 2 minutes. Your memories are safe and unchanged.</p>
  <div class="acts"><button class="btn primary">{ic("retry")}Try again</button></div>
</div></div>""",
        nav="memory",
    ),
)


# 10 Settings ---------------------------------------------------------------------------
OTHER_ROWS = (
    f'<tr><td>{who("Iris", True)}</td><td>Works on cards</td><td>now</td><td style="text-align:right">{{i}}</td></tr>'
    f'<tr><td>{who("Otto", True)}</td><td>Works on cards</td><td>now</td><td style="text-align:right">{{o}}</td></tr>'
    f'<tr><td>{who("Juno", True)}</td><td>Works on cards</td><td>5 min ago</td><td style="text-align:right">{{j}}</td></tr>'
)
LOCAL_FIELDS = (
    '<span class="sub-fields"><label for="model">Model</label><span class="selwrap"><select id="model" class="selectbox">'
    "<option>llama-3.1-8b-instruct</option><option>qwen-2.5-7b-instruct</option></select>"
    + ic("chev")
    + "</span><span>Status</span>{status}</span>"
)
HOSTED_OFF = '<div class="radio"><input type="radio" name="mode" id="m-hosted"><span><label for="m-hosted"><b>Hosted provider</b><small>Off. Sends chat messages to an outside AI service using a key your workspace owner saved. Choosing it shows the details before anything is sent.</small></label></span></div>'
HOSTED_ON = f"""<div class="radio on"><input type="radio" name="mode" id="m-hosted" checked autofocus><span><label for="m-hosted"><b>Hosted provider</b><small>Off until you press Turn on. Then it sends chat messages to an outside AI service using a key your workspace owner saved.</small></label>
        <span class="sub-fields"><label for="prov">Provider</label><span class="selwrap"><select id="prov" class="selectbox"><option>Choose a provider…</option><option>OpenAI-compatible service</option><option>Anthropic</option></select>{ic("chev")}</span>
        <span>Status</span><span class="tag lbl" style="justify-self:start">Off · not set up yet</span>
        <label for="keyname">Saved key</label><span class="selwrap"><select id="keyname" class="selectbox"><option>Choose a saved key…</option><option>“Team chat key” (saved by the owner)</option></select>{ic("chev")}</span></span>
        <span class="callout wait" style="margin-top:10px;display:flex"><span style="display:flex;flex-direction:column;gap:6px"><b style="color:var(--wait);display:flex;gap:6px;align-items:center">{ic("you")}Your chat leaves this machine</b><span style="color:var(--text)">Every chat message in Atlas goes to the provider you choose, to write the quick reply. Cards, memory and other projects are not sent. Nothing is sent until you press Turn on.</span>
        <span class="acts"><button class="btn sm" type="button">Cancel</button><button class="btn sm" type="button" aria-disabled="true">Turn on (choose a provider first)</button></span></span></span></span></div>"""


def settings_page(model_state="ok", hosted=False, solo=False):
    if model_state == "ok":
        status = f'<span class="tag ok" style="justify-self:start">{ic("check")}Reachable</span>'
        alert = ""
    else:
        status = (
            f'<span class="tag block" style="justify-self:start">{ic("block")}Not reachable</span>'
        )
        alert = f"""<div class="alert" role="alert" style="margin-top:10px"><h3>{ic("you")}No answer from the local model since 14:05</h3><span style="color:var(--text)">Commonplace tried the model on this computer and got no reply. Is it running? Until it answers, chat shows short receipts instead of answers; nothing is sent anywhere else.</span><div class="acts"><button class="btn sm primary">{ic("retry")}Try again</button><button class="btn sm">Switch to Basic</button></div></div>"""

    def row_btn(n):
        return f'<button class="btn ghost sm" aria-label="Disconnect {n}…">Disconnect…</button>'

    return f"""<header class="bar"><h1>Atlas</h1><span class="sub">Project settings</span><span class="spacer"></span><span class="saved" role="status">{ic("check")}Changes save automatically · saved 14:31</span><a class="btn" href="#">{ic("back")}Back to board <kbd>G</kbd><kbd>B</kbd></a></header>
<div class="settings"><div class="settings-inner">
  <section class="set-sec" aria-labelledby="s1">
    <h2 id="s1">Orchestrator</h2>
    <p class="lead">The project's lead agent: it reads chat and turns requests into cards. Whenever at least one agent is connected there is exactly one orchestrator, chosen automatically: the agent connected longest. With no agent connected there is none, and chat requests wait.</p>
    <div class="orch-card">{av("A", True)}<div><b>Atlas orchestrator</b> <span class="tag ok">{ic("check")}Connected</span><small>Agent on “Maya's laptop” · last active 2 min ago · reads chat, makes cards</small></div><button class="btn sm" id="disc-orch">Disconnect…</button></div>
  </section>
  <section class="set-sec" aria-labelledby="s2">
    <h2 id="s2">Chat answers</h2>
    <p class="lead">Which model writes the quick reply you see in chat. The orchestrator still does the work.</p>
    <fieldset class="radios" style="border:0;padding:0;margin:0"><legend class="sr">Chat answer mode</legend>
      <div class="radio"><input type="radio" name="mode" id="m-basic"><span><label for="m-basic"><b>Basic</b><small>No model. Instead of an answer, a short receipt confirms your message reached the orchestrator.</small></label></span></div>
      <div class="radio{"" if hosted else " on"}"><input type="radio" name="mode" id="m-local"{"" if hosted else " checked"}><span><label for="m-local"><b>Local model</b><small>Runs on your machine. Nothing leaves it.</small></label>
        {"" if hosted else LOCAL_FIELDS.format(status=status) + alert}</span></div>
      {HOSTED_ON if hosted else HOSTED_OFF}
    </fieldset>
  </section>
  <section class="set-sec" aria-labelledby="s3">
    <h2 id="s3">Agents</h2>
    <div class="inline-row"><span id="ma" style="font-weight:600">Max agents working at once</span><div class="stepper" role="group" aria-labelledby="ma"><button aria-label="One fewer">−</button><output aria-live="polite">3</output><button aria-label="One more">+</button></div><span style="color:var(--text-2);font-size:13px">Extra cards wait in Todo.</span></div>
    <table class="agents-t"><caption class="sr">Connected agents</caption><thead><tr><th>Agent</th><th>Role</th><th>Last active</th><th><span class="sr">Actions</span></th></tr></thead><tbody>
      <tr><td>{who("Atlas orchestrator", True)} <span style="color:var(--text-3);font-size:12.5px">(Maya's laptop)</span></td><td>Orchestrator</td><td>2 min ago</td><td style="text-align:right;color:var(--text-3);font-size:12.5px">Automatic · see above</td></tr>
{"" if solo else OTHER_ROWS.format(i=row_btn("Iris"), o=row_btn("Otto"), j=row_btn("Juno"))}
    </tbody></table>
    <div><button class="btn">{ic("plus")}Connect an agent</button></div>
  </section>
</div></div>"""


screens["10-settings"] = page("Project settings", shell(settings_page(), None, nav="none"))
screens["10b-settings-model-unreachable"] = page(
    "Project settings: model not reachable", shell(settings_page("down"), None, nav="none")
)
screens["10c-disconnect-orchestrator"] = page(
    "Disconnect the orchestrator",
    shell(
        settings_page(),
        None,
        nav="none",
        inert=True,
        extra=dialog(
            "Disconnect Atlas orchestrator?",
            """<div style="display:flex;flex-direction:column;gap:8px">
  <p><b>What happens:</b></p>
  <ul style="margin:0;padding-left:18px;display:flex;flex-direction:column;gap:6px">
    <li><b>Iris becomes the orchestrator</b> automatically. Iris has been connected longest of the other agents.</li>
    <li>Cards, chat and memory stay exactly as they are.</li>
    <li>Atlas orchestrator's setup stops working. To use it again, connect it as a new agent.</li>
  </ul></div>""",
            '<button class="btn is-focus" autofocus>Cancel <kbd>Esc</kbd></button><button class="btn danger">Disconnect</button>',
            alert=True,
            ret="disc-orch",
        ),
    ),
)
SOLO_AGENTS = [("atlas", "Atlas", "", "#2E43C0", 0, "1 agent")] + PROJECTS[1:]
screens["10d-disconnect-last-agent"] = page(
    "Disconnect the only agent",
    shell(
        settings_page(solo=True),
        None,
        nav="none",
        inert=True,
        extra=dialog(
            "Disconnect Atlas orchestrator?",
            f"""<div style="display:flex;flex-direction:column;gap:8px">
  <div class="alert" role="note"><h3>{ic("you")}It is the only agent in Atlas</h3><span style="color:var(--text)">No other agent can take over.</span></div>
  <p><b>What happens:</b></p>
  <ul style="margin:0;padding-left:18px;display:flex;flex-direction:column;gap:6px">
    <li>Atlas has <b>no orchestrator</b> until an agent connects. The next one you connect becomes it automatically.</li>
    <li>Chat still works: new requests wait, as before your first agent (you can turn any into a card yourself).</li>
    <li>Cards, chat and memory stay exactly as they are.</li>
  </ul></div>""",
            '<button class="btn is-focus" autofocus>Cancel <kbd>Esc</kbd></button><button class="btn">Connect another agent first</button><button class="btn danger">Disconnect</button>',
            alert=True,
            ret="disc-orch",
        ),
        projects=SOLO_AGENTS,
    ),
)
screens["10e-settings-hosted"] = page(
    "Project settings: hosted provider", shell(settings_page(hosted=True), None, nav="none")
)

# 11 Command palette -----------------------------------------------------------------------
pal = f"""<div class="scrim" aria-hidden="true"></div>
<div class="palette" role="dialog" aria-modal="true" aria-label="Command palette" data-return-focus="cmdk">
  <div class="pin">{ic("search")}<input type="text" role="combobox" aria-label="Search or run an action" aria-expanded="true" aria-controls="pl" aria-activedescendant="o1" aria-autocomplete="list" value="new" autofocus><span class="scope">in Atlas</span></div>
  <ul class="pl" id="pl" role="listbox" aria-label="Results">
    <li class="gh" role="presentation">Actions</li>
    <li class="opt" role="option" id="o1" aria-selected="true">{ic("plus")}<span><b>New</b> card in Atlas</span><span class="r"><kbd>C</kbd></span></li>
    <li class="opt" role="option" aria-selected="false">{ic("chat")}<span><b>New</b> request to the orchestrator</span><span class="r"><kbd>/</kbd></span></li>
    <li class="opt" role="option" aria-selected="false">{ic("project")}<span><b>New</b> project</span><span class="r"></span></li>
    <li class="gh" role="presentation">Cards</li>
    <li class="opt" role="option" aria-selected="false">{ic("card")}<span><span class="cid">A-134</span> Draft beta 0.9 release notes<span class="match">state: <b>New</b>, from chat 14:03</span></span><span class="r">Todo</span></li>
    <li class="opt" role="option" aria-selected="false">{ic("card")}<span><span class="cid">A-135</span> Check the sign-up email on 3 test domains<span class="match">state: <b>New</b>, from chat 14:03</span></span><span class="r">Todo</span></li>
    <li class="gh" role="presentation">Go to</li>
    <li class="opt" role="option" aria-selected="false">{ic("inbox")}<span>Needs me</span><span class="r"><span class="count need">3</span><kbd>G</kbd><kbd>N</kbd></span></li>
    <li class="opt" role="option" aria-selected="false">{ic("memory")}<span>Search memory for “new”</span><span class="r"><kbd>G</kbd><kbd>M</kbd></span></li>
  </ul>
  <div class="pfoot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> run</span><span><kbd>Esc</kbd> close</span><span style="margin-left:auto">Every action except Approve and Decline, which stay in Needs me</span></div>
</div>"""
screens["11-command-palette"] = page(
    "Command palette", shell(board_bar() + board(todo06_plain), card_detail, extra=pal, inert=True)
)

pal_card = f"""<div class="scrim" aria-hidden="true"></div>
<div class="palette" role="dialog" aria-modal="true" aria-label="Command palette" data-return-focus="cmdk">
  <div class="pin">{ic("search")}<input type="text" role="combobox" aria-label="Search or run an action" aria-expanded="true" aria-controls="pl2" aria-activedescendant="p1" aria-autocomplete="list" placeholder="Actions for A-121, or search everything" autofocus><span class="scope">A-121 selected</span></div>
  <ul class="pl" id="pl2" role="listbox" aria-label="Results">
    <li class="gh" role="presentation">A-121 Offline mode: queue edits…</li>
    <li class="opt" role="option" id="p1" aria-selected="true">{ic("arrow")}<span>Move to…</span><span class="r">Todo · Review · Done <kbd>M</kbd></span></li>
    <li class="opt" role="option" aria-selected="false">{ic("edit")}<span>Edit title</span><span class="r"><kbd>E</kbd></span></li>
    <li class="opt" role="option" aria-selected="false">{ic("bell")}<span>Remind Leon (waiting on him)</span><span class="r"></span></li>
    <li class="opt" role="option" aria-selected="false">{ic("memory")}<span>Search memory for this blocker</span><span class="r"></span></li>
    <li class="opt" role="option" aria-selected="false">{ic("clock")}<span>Change due date</span><span class="r">Fri 6 Nov</span></li>
    <li class="opt" role="option" aria-selected="false">{ic("link")}<span>Copy link</span><span class="r"></span></li>
        <li class="gh" role="presentation">Everywhere</li>
    <li class="opt" role="option" aria-selected="false">{ic("plus")}<span>New card</span><span class="r"><kbd>C</kbd></span></li>
    <li class="opt" role="option" aria-selected="false">{ic("inbox")}<span>Go to Needs me</span><span class="r"><kbd>G</kbd><kbd>N</kbd></span></li>
  </ul>
  <div class="pfoot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> run</span><span><kbd>Esc</kbd> close</span><span style="margin-left:auto">Actions for the selected card come first</span></div>
</div>"""
screens["11b-command-palette-card"] = page(
    "Command palette: card actions",
    shell(board_bar() + board(todo06_plain), card_detail, extra=pal_card, inert=True),
)


KROWS = [
    ("⌘K / Ctrl K", "Search or do (palette)", False),
    ("C", "New card", True),
    ("/", "Open the chat and put the cursor in it", True),
    ("G then B / N / M", "Go to board / Needs me / memory", True),
    ("J / K", "Next / previous item", True),
    ("E", "Edit the selected field", True),
    ("M", "Move card to…", True),
    ("R", "Ask a question (Needs me)", True),
    ("Y / L", "Copy exact words / link to card (memory)", True),
    ("Z", "Undo or stop (while the toast shows)", True),
    ("]", "Close the chat, or hide the side panel", True),
    ("Esc", "Close a dialog or the palette", False),
]
krows = "".join(
    f"<tr><td><kbd>{k}</kbd></td><td>{a}</td><td>"
    + (
        f'<button class="btn ghost sm" aria-label="Change shortcut for {a}">Change…</button>'
        if single
        else '<span style="color:var(--text-3);font-size:12.5px">Always on</span>'
    )
    + "</td></tr>"
    for k, a, single in KROWS
)
screens["12-shortcuts"] = page(
    "Keyboard shortcuts",
    shell(
        board_bar() + board(todo06_plain),
        card_detail,
        inert=True,
        extra=dialog(
            "Keyboard shortcuts",
            f"""<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 12px;border-radius:8px;background:var(--pane);border:1px solid var(--line)">
  <span><b id="sk-l">Single-key shortcuts</b><br><span style="font-size:13px;color:var(--text-2)">Turn off if you use voice control or a screen reader that sends letters. ⌘K / Ctrl K and Esc keep working.</span></span>
  <button class="switch is-focus" role="switch" aria-checked="true" aria-labelledby="sk-l" autofocus><span class="track" aria-hidden="true"></span><span class="state">On</span></button></div>
  <p style="font-size:13px;color:var(--text-2)">Single keys never fire while you type in a field. <b>Approve and Decline have no shortcut</b>; they always need the button.</p>
  <table class="kt"><caption class="sr">Shortcuts</caption><thead><tr><th>Key</th><th>Action</th><th><span class="sr">Change</span></th></tr></thead><tbody>{krows}</tbody></table>""",
            '<button class="btn">Reset to defaults</button><button class="btn primary">Done <kbd>Esc</kbd></button>',
            wide=True,
            ret="shortcuts-link",
        ),
    ),
)
screens["04c-board-tablet-panel"] = page(
    "Board on a tablet: details panel open",
    shell(
        board_bar(chip="new").replace(
            'aria-controls="ctx" aria-expanded="false"', 'aria-controls="ctx" aria-expanded="true"'
        )
        + board(todo04),
        card_detail.replace(
            '<aside class="context" id="ctx" aria-label="Context panel">',
            '<aside class="context" id="ctx" aria-label="Context panel" role="dialog" aria-modal="true" data-return-focus="details-btn">',
        ).replace(
            '<button class="hide" aria-keyshortcuts="]">',
            '<button class="hide is-focus" aria-keyshortcuts="]" autofocus>',
        ),
        panel_open=True,
    ),
)


# ---------------------------------------------------------------- mobile


def m_top(title="Atlas"):
    sw = (
        ""
        if title == "All projects"
        else '<span class="sw" style="width:12px;height:12px;border-radius:3px;background:#2E43C0" aria-hidden="true"></span>'
    )
    return f"""<header class="m-top">
  <button class="projbtn" aria-label="Project: {title}. Switch project">{sw}{title}{ic("chev")}</button>
  <span class="spacer"></span>
  <button class="iconbtn" aria-label="Search or do (command palette)">{ic("search")}</button>
  <button class="iconbtn" aria-label="Project settings">{ic("settings")}</button>
</header>"""


def m_tabs(cur, needs=3, chat_new=False):
    out = []
    for key, label, icn in (
        ("board", "Board", "board"),
        ("chat", "Chat", "chat"),
        ("needs", "Needs me", "inbox"),
        ("memory", "Memory", "memory"),
    ):
        c = ' aria-current="page"' if key == cur else ""
        b = (
            f'<span class="badge" aria-label="{needs} items">{needs}</span>'
            if key == "needs" and needs
            else ""
        )
        if key == "chat":
            # Chat opens as a full-screen sheet over the board, with a clear way back.
            b = (
                '<span class="badge newb">new<span class="sr">: 1 new reply</span></span>'
                if chat_new
                else ""
            )
            out.append(
                f'<a href="m10-chat.html" id="m-chat-tab" aria-haspopup="dialog">{ic(icn)}{label}{b}</a>'
            )
            continue
        out.append(f'<a href="#"{c}>{ic(icn)}{label}{b}</a>')
    return f'<nav class="m-tabs" aria-label="Sections">{"".join(out)}</nav>'


def m_board(extra="", inert=False):
    return f"""<div class="m"{" inert" if inert else ""}>
{m_top()}
<div class="m-cols" role="tablist" aria-label="Columns"><button role="tab" aria-selected="false" aria-controls="colpanel" tabindex="-1">Todo 4</button><button role="tab" aria-selected="true" aria-controls="colpanel" id="t-doing">Doing 3</button><button role="tab" aria-selected="false" aria-controls="colpanel" tabindex="-1">Review 2</button><button role="tab" aria-selected="false" aria-controls="colpanel" tabindex="-1">Done 14</button></div>
<main class="m-body" id="main" style="position:relative"><div id="colpanel" role="tabpanel" aria-labelledby="t-doing" style="display:flex;flex-direction:column;gap:8px">
  <div class="m-hintrow"><p class="m-hint">Tap ⋯ on a card to move or edit it.</p><button class="btn primary m-add-inline">{ic("plus")}Add card</button></div>
  {c121(False, more=True)}
  {c118(more=True)}
  {c124(more=True)}
</div></main>
<button class="fab" id="fab">{ic("plus")}<span class="fab-t">Add card</span></button>
{m_tabs("board", chat_new=True)}
</div>{extra}"""


screens["m1-board"] = page("Board (mobile)", m_board(), mobile=True)

screens["m2-needs-me"] = page(
    "Needs me (mobile)",
    f"""<div class="m">
{m_top("All projects")}
<main class="m-body" id="main" style="padding:0">
  <h1 class="m-h1">Needs me</h1><p class="m-sub">3 items. Only what an agent can't do without you.</p>
  <div style="display:flex;flex-direction:column;gap:8px;padding:0 12px 12px">
    <a class="m-item" href="#"><div class="top"><span class="typ approve">Approval</span><span>Atlas · 12 min</span></div><h3>Send beta 0.9 to 40 testers?</h3><div class="sub">{who("Atlas orchestrator", True)}</div></a>
    <a class="m-item" href="#"><div class="top"><span class="typ review">Review</span><span>Atlas · 1 h</span></div><h3>A-115 Push-notification opt-in screen</h3><div class="sub">{who("Iris", True)}</div></a>
    <a class="m-item" href="#"><div class="top"><span class="typ decide">Decision</span><span>Harbor · 3 h</span></div><h3>Pick the homepage headline</h3><div class="sub">{who("Harbor orchestrator", True)}</div></a>
  </div>
</main>
{m_tabs("needs")}
</div>""",
    mobile=True,
)

screens["m3-needs-me-item"] = page(
    "Approval (mobile)",
    f"""<div class="m">
<header class="m-top"><button class="m-back">{ic("back")}Needs me</button><span class="spacer"></span><span style="font-size:13px;color:var(--text-3);padding-right:8px">1 of 3</span></header>
<main class="m-detail" id="main">
  <div style="display:flex;gap:8px;align-items:center"><span class="typ approve">Approval</span><span style="font-size:13px;color:var(--text-3)">Atlas · asked 14:09</span></div>
  <h2 tabindex="-1">Send beta 0.9 to 40 testers?</h2>
  <div class="src">{who("Atlas orchestrator", True)}<span class="sep">·</span><span class="verbatim">exact words</span></div>
  <p class="quote" style="font-size:15px;line-height:22px">{APPROVAL_QUOTE}</p>
  {linked_cards(mobile=True)}
  {SEND_FACT}
  <button class="btn" style="min-height:44px;justify-content:center">{ic("chat")}Ask a question</button>
</main>
<div class="m-actions"><button class="btn danger">{ic("x")}Decline</button><button class="btn primary" id="m-approve">{ic("check")}Approve and send…</button></div>
</div>""",
    mobile=True,
)

M3_BODY = screens["m3-needs-me-item"].split('<body class="mobile">', 1)[1].split("</body>", 1)[0]
screens["m5-approve-confirm"] = page(
    "Approval (mobile): confirm",
    M3_BODY.replace('<div class="m">', '<div class="m" inert>', 1)
    + f"""<div class="scrim" aria-hidden="true"></div>
<div class="sheet" role="alertdialog" aria-modal="true" aria-labelledby="mc-h" aria-describedby="mc-d" data-return-focus="m-approve">
  <span class="grab" aria-hidden="true"></span>
  <h2 id="mc-h">Send beta 0.9 to 40 testers?</h2>
  <p id="mc-d" style="font-size:15px;line-height:22px">Your approval reaches the orchestrator at <b>14:22</b>, 60 seconds after you confirm. Until then you can stop it here, and nothing is sent.</p>
  <div class="row2"><button class="btn is-focus" autofocus>Cancel</button><button class="btn primary">{ic("check")}Yes, approve</button></div>
</div>""",
    mobile=True,
)


def m_after(banner, label, label_cls):
    body = M3_BODY.replace(
        '<span class="typ approve">Approval</span>',
        f'<span class="typ {label_cls}">{label}</span>',
        1,
    )
    body = body.replace(
        '<main class="m-detail" id="main">', banner + '\n<main class="m-detail" id="main">', 1
    )
    return (
        body.split('<div class="m-actions">')[0]
        + f'<div class="m-actions"><a class="btn" href="#">{ic("back")}Needs me</a><a class="btn primary" href="#">Next item (2 of 3){ic("arrow")}</a></div>\n</div>'
    )


screens["m6-approved-countdown"] = page(
    "Approval (mobile): stop window",
    m_after(
        f"""<section class="m-banner" aria-labelledby="mb-h">
  <span style="display:flex;justify-content:space-between;align-items:center"><b id="mb-h" role="status">Approved. Not sent yet.</b><span class="count-big" role="timer" aria-live="off" aria-label="time left before release">0:48</span></span>
  <span style="font-size:14px">Commonplace holds it until <b>14:22</b>. Stop now and nothing is sent.</span>
  <button class="btn danger is-focus" autofocus style="background:#fff">{ic("stop")}Stop send</button>
</section>""",
        "Waiting",
        "pending",
    ),
    mobile=True,
)
screens["m7-send-stopped"] = page(
    "Approval (mobile): stopped",
    m_after(
        f"""<section class="m-banner done" role="status" aria-labelledby="mb-h">
  <b id="mb-h">{ic("check")} Stopped. Nothing was sent.</b>
  <span style="font-size:14px">The orchestrator never received your approval. The request is back in Needs me so you can decide again.</span>
</section>""",
        "Approval",
        "approve",
    )
    .replace("Next item (2 of 3)", "Decide again")
    .replace('<h2 tabindex="-1">', '<h2 tabindex="-1" data-autofocus>'),
    mobile=True,
)
screens["m8-send-too-late"] = page(
    "Approval (mobile): released, too late to stop",
    m_after(
        f"""<section class="m-banner ok" aria-labelledby="mb-h">
  <b id="mb-h">Released to the orchestrator at 14:22</b>
  <span style="font-size:14px">The 60 seconds have passed, so this can't be stopped here anymore. The orchestrator reported at 14:23: “Build 0.9 sent to 40 of 40 testers.” To change something, ask the orchestrator.</span>
  <a class="btn is-focus" href="#" autofocus style="background:#fff">{ic("chat")}Ask the orchestrator</a>
</section>""",
        "Released",
        "done",
    ),
    mobile=True,
)
screens["m9-card-menu"] = page(
    "Card actions (mobile)",
    "",
    mobile=True,
)

screens["m4-add-card"] = page(
    "Add a card (mobile)",
    m_board(
        extra=f"""<div class="scrim" aria-hidden="true"></div>
<form class="sheet" role="dialog" aria-modal="true" aria-labelledby="sh-h" data-return-focus="fab">
  <span class="grab" aria-hidden="true"></span>
  <h2 id="sh-h">New card in Atlas · Todo</h2>
  <label class="lbl">Title<input class="field is-focus" value="Collect crash logs from beta group 2" autofocus></label>
  <label class="lbl">Details <span class="help">Optional</span><textarea class="field" rows="2"></textarea></label>
  <div class="chips" role="group" aria-label="Card fields"><button class="chip" type="button">Who: <b>Any agent</b></button><button class="chip" type="button">Label: <b>none</b></button><button class="chip" type="button">Due: <b>none</b></button></div>
  <div class="row2"><button class="btn" type="button">Cancel</button><button class="btn primary" type="button">{ic("plus")}Add card</button></div>
</form>""",
        inert=True,
    ),
    mobile=True,
)

screens["m9-card-menu"] = page(
    "Card actions (mobile)",
    m_board(
        extra=f"""<div class="scrim" aria-hidden="true"></div>
<div class="sheet menu-sheet" role="dialog" aria-modal="true" aria-labelledby="cm-h" data-return-focus="more-A-121">
  <span class="grab" aria-hidden="true"></span>
  <h2 id="cm-h">A-121 Offline mode: queue edits…</h2>
  <p style="font-size:13px;font-weight:650;color:var(--text-3);margin-bottom:-6px">MOVE TO</p>
  <div role="group" aria-label="Move to column">
    <button class="opt is-focus" autofocus>{ic("arrow")}Todo</button>
    <button class="opt" aria-current="true">{ic("check")}Doing <span class="sub">now here</span></button>
    <button class="opt">{ic("arrow")}Review</button>
    <button class="opt">{ic("arrow")}Done</button>
  </div>
  <div role="group" aria-label="Other actions">
    <button class="opt">{ic("edit")}Edit title</button>
    <button class="opt">{ic("bell")}Remind Leon <span class="sub">waiting on him</span></button>
    <button class="opt">{ic("link")}Copy link</button>
  </div>
  <button class="btn" style="min-height:48px;justify-content:center;font-size:16px">Close</button>
</div>""",
        inert=True,
    ),
    mobile=True,
)


# Mobile chat: full-screen sheet over the board, "Back to board" top left -------------------
def m_ref(cid, title, where, tag):
    return (
        f'<a href="m11-board-from-chat.html" class="m-ref">{tag}<span><span class="cid">{cid}</span> {title}</span>'
        f'<span class="where">{where} · Show {ic("arrow")}</span></a>'
    )


M_CHAT = f"""<div class="m-chat" role="dialog" aria-modal="true" aria-labelledby="mch-h" data-return-focus="m-chat-tab">
  <header class="m-top"><a class="m-back is-focus" href="m1-board.html" id="m-chat-back" autofocus>{ic("back")}Back to board</a><span class="spacer"></span></header>
  <div class="m-chat-h"><span class="ch-ic" aria-hidden="true">{ic("chat")}</span><div><h2 id="mch-h">Atlas chat</h2><p>Orchestrator connected · it answers here</p></div></div>
  <div class="m-chat-log" role="log" aria-label="Messages in Atlas chat"><div class="msgs">
    <p class="daysep">Today</p>
    {MAYA_ASK}
    <p class="newsep" role="separator">New since you last looked</p>
    <div class="msg">{av("A", True, lg=True)}<div><div class="msg-h"><b>Atlas orchestrator</b><span class="tag agent">agent</span><time>14:03</time></div>
      <div class="msg-b"><p>Yes. I made two cards and linked the bug that's already in progress. Release notes wait until the fix is checked.</p>
      <div class="made" aria-label="Cards from this request"><div class="made-h"><span>From this request</span><span>2 new · 1 linked</span></div>
        {m_ref("A-134", "Draft beta 0.9 release notes", "Todo", '<span class="tag new">New</span>')}
        {m_ref("A-135", "Check the sign-up email on 3 test domains", "Todo", '<span class="tag new">New</span>')}
        {m_ref("A-118", "Fix sign-up email not arriving…", "Doing", '<span class="tag lbl">Linked</span>')}
      </div>
      <p>When all three are done I'll ask you to approve sending beta 0.9 to the testers. That will show up in Needs me.</p></div></div></div>
  </div></div>
  <div class="m-chat-in"><label class="sr" for="mci">Message to Atlas chat</label><div class="box"><textarea id="mci" rows="1" placeholder="Ask for something…"></textarea><button class="btn primary" aria-label="Send">{ic("send")}</button></div></div>
</div>"""
screens["m10-chat"] = page("Chat (mobile)", m_board(extra=M_CHAT, inert=True), mobile=True)

c134m = card(
    "A-134",
    "Draft beta 0.9 release notes",
    OPEN,
    T_FROM,
    "is-new is-linked is-selected",
    state="New, from chat 14:03",
    more=True,
)
c135m = card(
    "A-135",
    "Check the sign-up email on 3 test domains",
    OPEN,
    T_FROM,
    "is-new",
    "Starts after A-118 is done",
    state="New, from chat 14:03",
    more=True,
    dep=True,
)
C_131m = card("A-131", "Write onboarding copy for the invite screen", OPEN, lbl("copy"), more=True)
screens["m11-board-from-chat"] = page(
    "Board (mobile), card shown from chat",
    f"""<div class="m">
{m_top()}
<div class="m-frombar"><span role="status">Showing <b>A-134</b>, made in chat at 14:03</span><a class="btn" href="m10-chat.html">{ic("chat")}Back to chat</a></div>
<div class="m-cols" role="tablist" aria-label="Columns"><button role="tab" aria-selected="true" aria-controls="colpanel" id="t-todo">Todo 6</button><button role="tab" aria-selected="false" aria-controls="colpanel" tabindex="-1">Doing 3</button><button role="tab" aria-selected="false" aria-controls="colpanel" tabindex="-1">Review 2</button><button role="tab" aria-selected="false" aria-controls="colpanel" tabindex="-1">Done 14</button></div>
<main class="m-body" id="main" style="position:relative"><div id="colpanel" role="tabpanel" aria-labelledby="t-todo" style="display:flex;flex-direction:column;gap:8px">
  {c134m.replace('tabindex="0"', 'tabindex="0" data-autofocus', 1)}
  {c135m}
  {C_131m}
  <a class="more" href="#">Show 3 more</a>
</div></main>
<button class="fab" id="fab">{ic("plus")}<span class="fab-t">Add card</span></button>
{m_tabs("board")}
</div>""",
    mobile=True,
)

for name, html in screens.items():
    (OUT / f"{name}.html").write_text(html)

# ---------------------------------------------------------------- shots manifest + index
SHOTS = []
for name in screens:
    if name == "04c-board-tablet-panel":
        continue
    if name.startswith("m"):
        SHOTS.append((name, name, 390, 844, 2))
    elif name.startswith("10") and name not in (
        "10c-disconnect-orchestrator",
        "10d-disconnect-last-agent",
    ):
        SHOTS.append((name, name, 1440, 1180, 1))
    else:
        SHOTS.append((name, name, 1440, 900, 1))
SHOTS += [
    ("04-board", "r2-04-board-1024", 1024, 768, 1),
    ("06c-chat-answer", "r-06c-chat-1024", 1024, 768, 1),
    ("04c-board-tablet-panel", "r2-04-board-1024-panel", 1024, 768, 1),
    ("04-board", "r1-04-board-320", 320, 2600, 2),
    ("m1-board", "r2-m1-board-320", 320, 640, 2),
    ("m3-needs-me-item", "r1-m3-needs-me-item-360", 360, 740, 2),
]
(ROOT / "_src" / "shots.txt").write_text(
    "".join(f"{a} {b} {w} {h} {d}\n" for a, b, w, h, d in SHOTS)
)

GROUPS = [
    (
        "1 · Start",
        ["01-signup", "01b-check-email", "01c-link-expired", "02-create-project", "03-board-empty"],
    ),
    (
        "1b · First chat and connecting an agent",
        [
            "03b-chat-no-agent",
            "03c-connect-agent",
            "03c2-connect-waiting",
            "03d-connect-done",
            "03e-connect-failed",
        ],
    ),
    ("2 · Board (default view)", ["04-board"]),
    ("3 · Add a card", ["05-add-card", "05b-card-added"]),
    (
        "4 · Talk to the orchestrator (Chat toggle)",
        [
            "06a-chat-open",
            "06b-chat-thinking",
            "06c-chat-answer",
            "06d-chat-error",
            "06e-chat-empty",
        ],
    ),
    (
        "5 · Needs me",
        [
            "07-needs-me",
            "07d-needs-me-ask",
            "07c-approve-confirm",
            "08-needs-me-done",
            "07b-needs-me-empty",
        ],
    ),
    (
        "6 · Find a memory",
        [
            "09-memory",
            "09f-memory-ask",
            "09e-memory-searching",
            "09b-memory-no-results",
            "09c-memory-empty",
            "09d-memory-error",
        ],
    ),
    (
        "Settings",
        [
            "10-settings",
            "10b-settings-model-unreachable",
            "10e-settings-hosted",
            "10c-disconnect-orchestrator",
            "10d-disconnect-last-agent",
        ],
    ),
    ("Keyboard", ["11-command-palette", "11b-command-palette-card", "12-shortcuts"]),
    (
        "Mobile (390×844 @2x)",
        [
            "m1-board",
            "m10-chat",
            "m11-board-from-chat",
            "m9-card-menu",
            "m4-add-card",
            "m2-needs-me",
            "m3-needs-me-item",
            "m5-approve-confirm",
            "m6-approved-countdown",
            "m7-send-stopped",
            "m8-send-too-late",
        ],
    ),
    (
        "Reflow (same markup, other widths)",
        [
            "r2-04-board-1024",
            "r-06c-chat-1024",
            "r2-04-board-1024-panel",
            "r1-04-board-320",
            "r2-m1-board-320",
            "r1-m3-needs-me-item-360",
        ],
    ),
]
src = {b: a for a, b, *_ in SHOTS}
sec = []
for g, names in GROUPS:
    cells = "".join(
        f'<li><a href="screens/{src[n]}.html"><img src="shots/{n}.png" alt="Screenshot: {n}" loading="lazy" class="{"mob" if n.startswith(("m", "r1-m", "r2-m", "r1-04-board-320")) else ""}"></a>'
        f'<span><b>{n}</b> · <a href="shots/{n}.png">PNG</a></span></li>'
        for n in names
    )
    sec.append(f"<section><h2>{g}</h2><ul>{cells}</ul></section>")
(ROOT / "index.html").write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Commonplace · Direction B Workbench + chat · contact sheet</title>
<style>
body{{margin:0;padding:32px 40px;font:14px/20px "Ubuntu Sans",system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:#F3F3EF;color:#17181C}}
h1{{font-size:24px;margin:0 0 4px}} p{{margin:0 0 20px;color:#4A4C55;max-width:760px}}
h2{{font-size:15px;margin:24px 0 10px}} ul{{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:16px}}
li{{display:flex;flex-direction:column;gap:6px;background:#fff;border:1px solid #DEDED6;border-radius:8px;padding:8px}}
img{{width:360px;height:225px;object-fit:cover;object-position:top left;border:1px solid #DEDED6;border-radius:4px;display:block}}
img.mob{{width:120px;height:260px}} a{{color:#2E43C0}} a:focus-visible{{outline:3px solid #2E43C0;outline-offset:2px}}
li span{{font-size:13px;color:#4A4C55}}
</style></head><body>
<h1>Direction B + chat · Workbench with the orchestrator chat (round 1)</h1>
<p>B Workbench, plus one element from C: the project chat sits next to the board behind one labelled <b>Chat</b> toggle in the board header. Click a thumbnail for the live HTML screen. Flows and notes: <a href="README.md">README.md</a>.</p>
{"".join(sec)}
</body></html>
""")
print(len(screens), "screens,", len(SHOTS), "shots")
