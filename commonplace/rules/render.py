"""Pure, bounded rule rendering shared by every instruction surface."""

import json
import re
import unicodedata

REPLY_BYTES = 24_000
RULE_BYTES = 6_000
TOO_LARGE = "rules_seq too_large: the rule set is too large for one reply; ask the owner to trim it"
HEADINGS = (
    ("limit", "Hard limits"),
    ("permission", "Permissions"),
    ("instruction", "Standing instructions"),
    ("style", "Style"),
)


def length(text):
    return len(text.encode("utf-8"))


def quote(text):
    def escape(char):
        code = ord(char)
        if code <= 0xFFFF:
            return f"\\u{code:04x}"
        code -= 0x10000
        return f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 1023):04x}"

    return "".join(
        escape(c) if c in "<>&@" or unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp", "Cs") else c
        for c in json.dumps(text, ensure_ascii=False)
    )


def label(row):
    if row["scope"] == "global":
        return ""
    value = row["scope_ref"]
    value = value if re.fullmatch(r"[A-Za-z0-9._/:-]+", value) else quote(value)
    return f"[{row['scope']} {value}] "


def md(rows):
    lines = []
    for kind, heading in HEADINGS:
        selected = [r for r in rows if r["kind"] == kind]
        if selected:
            lines += [f"## {heading}", ""]
            lines += ["- " + label(r) + quote(r["text"]) for r in selected]
            lines.append("")
    return "\n".join(lines)


def text(rows):
    return "\n".join(
        f"{r['key']} [{r['kind']} {r['scope']} {r['status']} {r['source']} v{r['version']}] "
        + quote(r["text"])
        for r in rows
    )


def effective(rows, seq, rules_seq, *, format="text", mode="off", budget=REPLY_BYTES):
    """Count is the total authorized set; truncation never splits a rule."""
    render = md if format == "md" else text
    header = f"effective rules: {len(rows)}"
    marker = f"rules_seq {rules_seq}" if mode != "off" else "rules_seq off"

    def reply(selected, tail):
        return "\n".join([header, render(selected), f"seq {seq}", tail])

    complete = reply(rows, marker)
    if length(complete) <= budget:
        return complete, "served"
    shown = []
    for row in rows:
        candidate = [*shown, row]
        tail = f"Incomplete: {len(rows) - len(candidate)} rules not shown.\n{TOO_LARGE}"
        if length(reply(candidate, tail)) <= budget:
            shown.append(row)
    tail = f"Incomplete: {len(rows) - len(shown)} rules not shown.\n{TOO_LARGE}"
    return reply(shown, tail), "too_large"
