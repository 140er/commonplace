"""Compare #hex colours between this design folder and a theme folder.

Usage: python3 -I _src/hexcheck.py <design-folder> <theme-folder>
Scope: design folder = every .css, .html, .js and .py file (the screens, contact sheet, script and generator);
theme folder = every file of any type (skipping .git), read as text. Values are lower-cased and 3/4-digit forms
expanded; the alpha part of 8-digit values is ignored.
"""

import pathlib
import re
import sys

HEX = re.compile(r"#[0-9a-fA-F]{3,8}(?![\w-])")  # "#add-card" is an id, not a colour


def norm(h):
    h = h.lower().lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    return "#" + h[:6] if len(h) in (6, 8) else None


def collect(root, exts=None):
    found, files = set(), 0
    for p in pathlib.Path(root).rglob("*"):
        if not p.is_file() or ".git" in p.parts or (exts and p.suffix.lower() not in exts):
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        files += 1
        found |= {n for n in map(norm, HEX.findall(text)) if n}
    return found, files


ours, nf = collect(sys.argv[1], {".css", ".html", ".js", ".py"})
theirs, tf = collect(sys.argv[2])
print(f"design: {len(ours)} colours in {nf} files (.css .html .js .py)")
print(f"theme:  {len(theirs)} colours in {tf} files (all files, .git skipped)")
print("shared:", sorted(ours & theirs) or "none")
