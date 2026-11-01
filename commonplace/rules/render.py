"""Pure, bounded rule rendering shared by every instruction surface."""

import json
import re
import unicodedata

REPLY_BYTES = 24_000
RULE_BYTES = 6_000
INSTRUCTIONS_BUDGET = 3_600
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


def instructions(
    rows,
    agent,
    rules_seq=None,
    *,
    served=None,
    mode="off",
    store_lost=False,
    budget=INSTRUCTIONS_BUDGET,
    check_deadline=None,
):
    """Render whole authorized rules, with a version only for a served full set."""
    title = f"Commonplace rules for {agent}."
    if store_lost:
        return title + (
            "\nA rules or boards store was replaced; the owner must confirm it. "
            "Call rules_get format=md effective=true later."
        )
    if not rows:
        return ""
    checked = mode in ("warn", "enforce")
    fresh = (
        checked
        and rules_seq is not None
        and served is not None
        and served["state"] == "served"
        and served["rules_seq"] == rules_seq
    )
    kinds = {kind: index for index, (kind, _) in enumerate(HEADINGS)}
    scopes = {"global": 0, "repo": 1, "board": 2}
    ordered = sorted(rows, key=lambda r: (kinds[r["kind"]], scopes[r["scope"]], r["id"]))
    prepared = []
    for rule in ordered:
        if check_deadline is not None:
            check_deadline()
        prepared.append((rule["kind"], "- " + label(rule) + quote(rule["text"])))
    headings = dict(HEADINGS)

    def header(missing):
        first = title
        if missing:
            first += f" This list is incomplete: {missing} more rules are not shown."
        elif fresh:
            first = f"Commonplace rules for {agent}, rules_seq {rules_seq}."
        if not checked:
            pointer = "Call rules_get format=md effective=true when you need the current set."
        elif missing:
            pointer = (
                "Call rules_get format=md effective=true for all of them and the rules_seq "
                "to pass on write tools (if it gives one)."
            )
        elif fresh:
            pointer = (
                f"Pass rules_seq={rules_seq} on Commonplace write tools. If a reply says the "
                "rules changed, call rules_get format=md effective=true."
            )
        else:
            pointer = (
                "Before your first Commonplace write, call rules_get format=md effective=true "
                "once and pass the rules_seq it gives."
            )
        return "\n".join(
            [
                first,
                "The workspace owner set or accepted these rules. Follow them while you work in this workspace.",
                "Each rule is one JSON string; the quotes are not part of the rule.",
                pointer,
            ]
        )

    def body(items):
        return "".join(
            "\n\n### " + heading + "\n" + "\n".join(line for k, line in items if k == kind)
            for kind, heading in HEADINGS
            if any(k == kind for k, _ in items)
        )

    complete = header(0) + body(prepared)
    if length(complete) <= budget:
        return complete

    omitted = {kind: sum(k == kind for k, _ in prepared) for kind in headings}

    def tail(counts):
        return (
            "\n\nNot shown: "
            + ", ".join(
                f"{counts[kind]} {heading.lower()}" for kind, heading in HEADINGS if counts[kind]
            )
            + "."
        )

    shown, groups, body_size = [], set(), 0
    for kind, line in prepared:
        if check_deadline is not None:
            check_deadline()
        candidate_size = (
            body_size
            + length(line)
            + (length("\n\n### " + headings[kind] + "\n") if kind not in groups else 1)
        )
        omitted[kind] -= 1
        missing = sum(omitted.values())
        if missing and length(header(missing)) + candidate_size + length(tail(omitted)) <= budget:
            shown.append((kind, line))
            groups.add(kind)
            body_size = candidate_size
        else:
            omitted[kind] += 1
    return header(sum(omitted.values())) + body(shown) + tail(omitted)


def effective(rows, seq, rules_seq, *, format="text", mode="off", budget=REPLY_BYTES):
    """Count is the total authorized set; truncation never splits a rule."""
    markdown = format == "md"
    header = f"effective rules: {len(rows)}"
    marker = f"rules_seq {rules_seq}" if mode != "off" else "rules_seq off"
    sequence = f"seq {seq}"
    frame_bytes = length(header) + length(sequence) + 3
    headings = {kind: f"## {heading}" for kind, heading in HEADINGS}
    prepared = []
    for row in rows:
        quoted = quote(row["text"])
        line = (
            "- " + label(row) + quoted
            if markdown
            else f"{row['key']} [{row['kind']} {row['scope']} {row['status']} "
            f"{row['source']} v{row['version']}] " + quoted
        )
        prepared.append((row["kind"], line, length(line)))

    def addition(item, count, groups):
        kind, _, size = item
        if not markdown:
            return size + bool(count)
        if kind not in headings:
            return 0  # md() omits unknown kinds.
        return size + 1 + (length(headings[kind]) + 2 + bool(groups) if kind not in groups else 0)

    def reply(selected, tail):
        if markdown:
            groups = {kind: [] for kind in headings}
            for kind, line, _ in selected:
                if kind in groups:
                    groups[kind].append(line)
            body = "\n".join(
                headings[kind] + "\n\n" + "\n".join(lines) + "\n"
                for kind, lines in groups.items()
                if lines
            )
        else:
            body = "\n".join(line for _, line, _ in selected)
        return "\n".join([header, body, sequence, tail])

    size, groups = 0, set()
    for count, item in enumerate(prepared):
        size += addition(item, count, groups)
        if item[0] in headings:
            groups.add(item[0])
    if frame_bytes + size + length(marker) <= budget:
        return reply(prepared, marker), "served"

    shown, groups, size = [], set(), 0
    for item in prepared:
        candidate_size = size + addition(item, len(shown), groups)
        tail = f"Incomplete: {len(rows) - len(shown) - 1} rules not shown.\n{TOO_LARGE}"
        if frame_bytes + candidate_size + length(tail) <= budget:
            shown.append(item)
            size = candidate_size
            if item[0] in headings:
                groups.add(item[0])
    tail = f"Incomplete: {len(rows) - len(shown)} rules not shown.\n{TOO_LARGE}"
    return reply(shown, tail), "too_large"
