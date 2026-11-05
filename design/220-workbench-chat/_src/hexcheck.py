import pathlib
import re
import sys


def norm(h):
    h = h.lower().lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    return "#" + h[:6] if len(h) in (6, 8) else None


def collect(root, exts):
    out = set()
    for p in pathlib.Path(root).rglob("*"):
        if (
            p.is_file()
            and p.suffix.lower() in exts
            and ".git" not in p.parts
            and "node_modules" not in p.parts
        ):
            try:
                t = p.read_text(errors="ignore")
            except Exception:
                continue
            for m in re.findall(r"#[0-9a-fA-F]{3,8}\b", t):
                n = norm(m)
                if n:
                    out.add(n)
    return out


ours = collect(sys.argv[1], {".css", ".html", ".js", ".py"})
theirs = collect(
    sys.argv[2],
    {
        ".css",
        ".html",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".json",
        ".scss",
        ".less",
        ".svg",
        ".py",
        ".md",
        ".toml",
        ".yaml",
        ".yml",
        ".txt",
        ".lua",
        ".ini",
        ".cfg",
        ".xml",
        ".qss",
    },
)
print("B-chat hex colours:", len(ours))
print("ev0lve-themes hex colours:", len(theirs))
print("shared:", sorted(ours & theirs))
