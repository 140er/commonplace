"""State-fact self-check across all screens: the same state must read the same everywhere.

Usage: python3 -I _src/factcheck.py   (run in the design folder)
"""

import pathlib
import re
import sys

D = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".") / "screens"
problems = []


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


for f in sorted(D.glob("*.html")):
    h = f.read_text()
    t = text(h)
    n = f.name
    # 1. A chat that has not answered never calls itself healthy, and states the same times.
    if "No answer yet" in t or "Not answering since" in t:
        if re.search(r"Orchestrator connected(?!,)", t):
            problems.append(f"{n}: says 'Orchestrator connected' next to a not-answering state")
        for m in re.findall(r"[Ll]ast active (?:at )?([0-9:]+|\d+ min ago)", t):
            if m != "14:03":
                problems.append(f"{n}: last active '{m}' (the not-answering state says 14:03)")
        if re.search(r"<td>Orchestrator</td><td>(?!14:03)", h):
            problems.append(f"{n}: agents table gives another last-active time")
        if "may be closed or" in t:
            problems.append(f"{n}: guesses 'may be closed or asleep'")
    # 2. The 12:41 reply is always a quick reply, the 14:03 answer always the orchestrator.
    for who_, time_ in re.findall(r'<div class="msg-h">(.*?)<time>(\d\d:\d\d)</time>', h):
        if time_ == "12:41" and "Quick reply" not in who_:
            problems.append(f"{n}: 12:41 message not labelled Quick reply")
        if time_ == "14:03" and "Atlas orchestrator" not in who_:
            problems.append(f"{n}: 14:03 answer not labelled Atlas orchestrator")
    # 3. Strips name the right message for the card they opened.
    for cid, msg in re.findall(
        r'id="from-chat"[^>]*data-row="(A-\d+)"[^>]*>.*?<span class="fc-text">([^<]*)', h
    ):
        want = "12:41 quick reply" if cid == "A-121" else "14:03 answer"
        if want not in msg:
            problems.append(f"{n}: strip for {cid} says '{msg}'")
    for cid, src in re.findall(r'data-card="(A-\d+)" data-src="([^"]*)"', h):
        if (cid == "A-121") != ("quick reply" in src):
            problems.append(f"{n}: row {cid} carries source '{src}'")
    # 4. An open chat control never also shows an unread count; no count where there is no orchestrator.
    for btn in re.findall(
        r'<button class="chat-toggle"[^>]*aria-expanded="true"[^>]*>.*?</button>', h
    ):
        if "new reply" in btn:
            problems.append(f"{n}: open chat control still shows an unread count")
    if "No orchestrator yet" in t and re.search(r'role="status">1 new reply', h):
        problems.append(f"{n}: unread count announced while there is no orchestrator")
    if 'class="m-chat"' in h and 'id="mch-h"' in h and re.search(r'role="status">1 new reply', h):
        problems.append(f"{n}: unread count announced behind an open phone chat")
    # 5. Composer, heading and log name the same project.
    for proj_label in re.findall(r"Message to (\w+) chat", h):
        heads = set(re.findall(r'<h2 id="(?:chat-h|mch-h)">(\w+) chat</h2>', h))
        if heads and proj_label not in heads:
            problems.append(f"{n}: composer says {proj_label}, heading says {heads}")
print("\n".join(problems) or "no contradictions found", f"({len(list(D.glob('*.html')))} screens)")
sys.exit(1 if problems else 0)
