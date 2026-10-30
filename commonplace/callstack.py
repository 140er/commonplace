"""A PR as a call tree: its new and changed functions, from entry point to callee.

    python -m commonplace.callstack --base origin/develop [--head HEAD]
    python -m commonplace.callstack --base origin/develop --card 142 [--hub URL --token T]

Reads ``git diff base...head`` in the current repository and prints JSON, or stores it
on a card (``callstack``). Python is parsed with ``ast``. JS/TS uses a masked-source
regex and brace matcher, so unusual syntax can be missed. Other files are listed flat.

Nested helper functions fold into the function around them. A call links two functions
only with evidence: ``self.x``/``this.x`` in the caller's class, a bare name in the
caller's file or imported by it, ``mod.x`` through an import, ``Class.x`` by class name.
Anything else is dropped, never guessed. Unchanged callers are added only when they are entry
points: ``main``, ``cmd_*``, ``handle_*``, ``do_*``, route/command/tool decorators, and
exported JS functions. Test files are listed flat, never in the tree.
"""

from __future__ import annotations

import argparse
import ast
import bisect
import json
import os
import posixpath
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import PurePosixPath

PY = {".py"}
JS = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts"}
MAX_BYTES = 512_000
SKIP_DIRS = {"node_modules", "vendor", "dist", "build", ".venv", "venv", "site-packages"}
ENTRY_DECORATOR = re.compile(
    r"route|\.(?:get|post|put|patch|delete|command|group|tool|hook)\b|command|register|tool\b"
)
ENTRY_NAME = re.compile(r"^(?:main|cmd_\w+|handle_?\w*|do_[A-Z]+|on[A-Z]\w*)$")
JS_KEYWORDS = set(
    "if for while switch catch function return with else do try typeof new await yield "
    "super import export delete void in of case throw class constructor".split()
)


@dataclass
class Func:
    file: str
    name: str  # qualified: Class.method, outer.inner
    line: int
    start: int  # first line, decorators included
    end: int
    params: str
    lang: str
    cls: str | None
    entry: bool
    calls: list = field(default_factory=list)  # (line, name, receiver)
    imports: dict = field(default_factory=dict)  # local name -> (module, imported name)
    change: str = "unchanged"

    @property
    def key(self):
        return f"{self.file}::{self.name}"

    @property
    def short(self):
        return self.name.rsplit(".", 1)[-1]

    @property
    def module(self):
        return module_of(self.file)


def module_of(path: str) -> str:
    """Import target of a file: "pkg/mod" for pkg/mod.py, "pkg" for pkg/__init__.py."""
    stem = str(PurePosixPath(path).with_suffix(""))
    return re.sub(r"/(?:__init__|index)$", "", stem)


def is_test(path: str) -> bool:
    p = PurePosixPath(path)
    return bool(
        {"tests", "test", "__tests__"} & set(p.parts[:-1])
        or re.match(r"(?:test_.*\.py|.*_test\.py|.*\.(?:test|spec)\.[cm]?[jt]sx?)$", p.name)
    )


def language(path: str) -> str | None:
    suffix = PurePosixPath(path).suffix
    return "py" if suffix in PY else "js" if suffix in JS else None


# ---------- Python ----------


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _py_params(fn) -> str:
    names = [a.arg for a in fn.args.posonlyargs + fn.args.args]
    if names and names[0] in ("self", "cls"):
        names = names[1:]
    if fn.args.vararg:
        names.append("*" + fn.args.vararg.arg)
    elif fn.args.kwonlyargs:
        names.append("*")
    names += [a.arg for a in fn.args.kwonlyargs]
    if fn.args.kwarg:
        names.append("**" + fn.args.kwarg.arg)
    return _clip(", ".join(names))


def _py_calls(fn):
    """Calls in the body, nested helper functions included."""
    calls, stack = [], list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.ClassDef):
            continue
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                calls.append((node.lineno, target.id, None))
            elif isinstance(target, ast.Attribute):
                receiver = target.value.id if isinstance(target.value, ast.Name) else "?"
                calls.append((node.lineno, target.attr, receiver))
        stack.extend(ast.iter_child_nodes(node))
    return sorted(calls)


def _py_imports(path: str, tree) -> dict:
    imports, package = {}, PurePosixPath(path).parent.parts
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    imports[alias.asname] = (alias.name.replace(".", "/"), None)
        elif isinstance(node, ast.ImportFrom):
            parts = list(package[: len(package) - node.level + 1]) if node.level else []
            base = "/".join(parts + (node.module.split(".") if node.module else []))
            for alias in node.names:
                imports[alias.asname or alias.name] = (base, alias.name)
    return imports


def python_functions(path: str, source: str) -> list[Func]:
    found, tree = [], ast.parse(source)
    imports = _py_imports(path, tree)

    def visit(node, prefix, cls):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.", child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = [ast.unparse(d) for d in child.decorator_list]
                found.append(
                    Func(
                        file=path,
                        name=prefix + child.name,
                        line=child.lineno,
                        start=min([child.lineno] + [d.lineno for d in child.decorator_list]),
                        end=child.end_lineno or child.lineno,
                        params=_py_params(child),
                        lang="py",
                        cls=cls,
                        entry=bool(ENTRY_NAME.match(child.name))
                        or any(ENTRY_DECORATOR.search(d) for d in decorators),
                        calls=_py_calls(child),
                        imports=imports,
                    )
                )
            else:
                visit(child, prefix, cls)

    visit(tree, "", None)
    return found


# ---------- JS / TS ----------


def mask_js(src: str) -> str:
    """Blank out comments, strings and template text so braces and names are code only."""
    out, i, n, templates, in_template = list(src), 0, len(src), [], False

    def blank(a, b):
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = src[i]
        if in_template:
            if c == "\\":
                blank(i, i + 2)
                i += 2
            elif c == "`":
                in_template, i = False, i + 1
            elif src.startswith("${", i):
                templates.append(0)
                in_template, i = False, i + 2
            else:
                blank(i, i + 1)
                i += 1
        elif src.startswith("//", i):
            end = src.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
        elif src.startswith("/*", i):
            end = src.find("*/", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] not in (c, "\n"):
                j += 2 if src[j] == "\\" else 1
            blank(i + 1, j)
            i = j + 1
        elif c == "`":
            in_template, i = True, i + 1
        else:
            if templates and c == "{":
                templates[-1] += 1
            elif templates and c == "}":
                if templates[-1] == 0:
                    templates.pop()
                    in_template = True
                else:
                    templates[-1] -= 1
            i += 1
    return "".join(out)


_NAME = r"[A-Za-z_$][\w$]*"
JS_FUNCTION = re.compile(
    rf"^\s*(?P<export>export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*(?P<name>{_NAME})"
    r"\s*(?:<[^>]*>)?\s*\((?P<params>[^)]*)"
)
JS_BINDING = re.compile(
    rf"^\s*(?P<export>export\s+)?(?:const|let|var)\s+(?P<name>{_NAME})\s*(?::[^=]+)?=\s*"
    rf"(?:async\s+)?(?:function\b[^(]*\((?P<params>[^)]*)|\((?P<arrow>[^)]*)\)\s*(?::[^=]+)?=>"
    rf"|(?P<single>{_NAME})\s*=>)"
)
JS_CLASS = re.compile(
    rf"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<name>{_NAME})"
)
JS_METHOD = re.compile(
    r"^\s*(?:(?:public|private|protected|static|async|override|get|set)\s+)*\*?\s*"
    rf"(?P<name>#?{_NAME})\s*(?:<[^>]*>)?\s*\((?P<params>[^)]*)\)\s*(?::[^{{;]+)?\{{"
)
JS_CALL = re.compile(rf"(?:(?P<recv>{_NAME})\s*(?:\?\.|\.)\s*)?(?P<name>{_NAME})\s*\(")


def _match_brace(masked: str, i: int) -> int:
    depth = 0
    for j in range(i, len(masked)):
        if masked[j] == "{":
            depth += 1
        elif masked[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(masked) - 1


def _body(masked: str, pos: int, arrow: bool) -> tuple[int, int]:
    """(body start, body end) offsets for a declaration starting at pos."""
    if arrow:
        pos = masked.find("=>", pos) + 2
        while pos < len(masked) and masked[pos] in " \t\n":
            pos += 1
        if not masked.startswith("{", pos):
            depth = 0
            for j in range(pos, len(masked)):
                c = masked[j]
                depth += c in "([{"
                depth -= c in ")]}"
                if depth < 0 or (depth == 0 and c in ";\n"):
                    return pos, j
            return pos, len(masked) - 1
        return pos, _match_brace(masked, pos)
    depth = 0
    for j in range(pos, len(masked)):
        c = masked[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "{" and depth == 0:
            return j, _match_brace(masked, j)
        elif c == ";" and depth == 0:
            return j, j
    return pos, pos


JS_IMPORT = re.compile(
    rf"import\s+(?:type\s+)?(?:{_NAME}\s*,?\s*)?(?:\{{(?P<named>[^}}]*)\}}|\*\s+as\s+(?P<ns>{_NAME}))?"
    r"\s*from\s*[\"'](?P<src>\.[^\"']*)[\"']"
)


def _js_imports(path: str, source: str) -> dict:
    imports = {}
    for m in JS_IMPORT.finditer(source):
        target = module_of(posixpath.normpath(posixpath.join(posixpath.dirname(path), m["src"])))
        if m["ns"]:
            imports[m["ns"]] = (target, None)
        for item in (m["named"] or "").split(","):
            names = item.split(" as ")
            if names[0].strip():
                imports[names[-1].strip()] = (target, names[0].strip())
    return imports


def js_functions(path: str, source: str) -> list[Func]:
    masked = mask_js(source)
    imports = _js_imports(path, source)
    lines = masked.split("\n")
    offsets, total = [], 0
    for text in lines:
        offsets.append(total)
        total += len(text) + 1
    line_of = lambda offset: bisect.bisect_right(offsets, offset) - 1  # noqa: E731
    classes = []  # (name, body start, body end)
    for k, text in enumerate(lines):
        m = JS_CLASS.match(text)
        if m and "{" in masked[offsets[k] :]:
            open_at = masked.index("{", offsets[k])
            classes.append((m["name"], open_at, _match_brace(masked, open_at)))
    found, bodies = [], []
    for k, text in enumerate(lines):
        pos = offsets[k]
        owner = next((c for c in reversed(classes) if c[1] < pos <= c[2]), None)
        m, arrow, cls, exported = JS_FUNCTION.match(text), False, None, False
        params = m["params"] if m else ""
        if m:
            exported = bool(m["export"])
        elif (m := JS_BINDING.match(text)) is not None:
            arrow, exported = m["params"] is None, bool(m["export"])
            params = next(p for p in (m["params"], m["arrow"], m["single"]) if p is not None)
        elif (
            owner
            and (m := JS_METHOD.match(text))
            and m["name"] not in JS_KEYWORDS - {"constructor"}
        ):
            depth = masked[owner[1] : pos].count("{") - masked[owner[1] : pos].count("}")
            if depth != 1:
                continue
            cls, params = owner[0], m["params"]
        else:
            continue
        name = m["name"]
        if (owner and not cls) or any(a < pos <= b for a, b in bodies):
            continue  # a local helper folds into the function around it
        body_start, body_end = _body(masked, pos + m.start("name"), arrow)
        bodies.append((body_start, body_end))
        body = masked[body_start : body_end + 1]
        calls = []
        for call in JS_CALL.finditer(body):
            before = masked[max(0, body_start + call.start() - 9) : body_start + call.start()]
            if call["name"] in JS_KEYWORDS or before.rstrip().endswith("function"):
                continue
            calls.append((line_of(body_start + call.start("name")) + 1, call["name"], call["recv"]))
        found.append(
            Func(
                file=path,
                name=f"{cls}.{name}" if cls else name,
                line=k + 1,
                start=k + 1,
                end=max(k + 1, line_of(body_end) + 1),
                params=_clip(params),
                lang="js",
                cls=cls,
                entry=exported or bool(ENTRY_NAME.match(name)),
                calls=calls,
                imports=imports,
            )
        )
    return found


def functions(path: str, source: str) -> list[Func] | None:
    """None when the language is unsupported or the file does not parse."""
    lang = language(path)
    try:
        if lang == "py":
            return python_functions(path, source)
        if lang == "js":
            return js_functions(path, source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    return None


# ---------- git ----------


def git(*args, cwd=None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, errors="replace"
    ).stdout


def blobs(rev: str, paths, cwd=None) -> dict[str, str]:
    paths = [p for p in paths if "\n" not in p]
    if not paths:
        return {}
    data = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=cwd,
        check=True,
        capture_output=True,
        input="".join(f"{rev}:{p}\n" for p in paths).encode(),
    ).stdout
    out, i = {}, 0
    for path in paths:
        newline = data.index(b"\n", i)
        header = data[i:newline].split()
        if header[-1] == b"missing" or len(header) < 3:
            i = newline + 1
            continue
        size = int(header[2])
        if header[1] == b"blob" and size <= MAX_BYTES:
            out[path] = data[newline + 1 : newline + 1 + size].decode("utf-8", "replace")
        i = newline + 1 + size + 1
    return out


def changed_files(base: str, head: str, cwd=None) -> list[tuple[str, str, str]]:
    """(status letter, old path, new path) for base...head, renames detected."""
    fields = git("diff", "--name-status", "-z", "-M", f"{base}...{head}", cwd=cwd).split("\0")
    out, i = [], 0
    while i < len(fields) and fields[i]:
        status = fields[i][0]
        if status in "RC":
            out.append((status, fields[i + 1], fields[i + 2]))
            i += 3
        else:
            out.append((status, fields[i + 1], fields[i + 1]))
            i += 2
    return out


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)


def touched_lines(base: str, head: str, old: str, new: str, cwd=None) -> tuple[set, set]:
    """Added or edited lines in the new file, and the lines that sit next to a deletion."""
    diff = git("diff", "-U0", "-M", "--no-color", f"{base}...{head}", "--", old, new, cwd=cwd)
    edited, deleted_after = set(), set()
    for m in HUNK.finditer(diff):
        start, count = int(m[1]), int(m[2] if m[2] is not None else 1)
        if count:
            edited.update(range(start, start + count))
        else:
            deleted_after.add(start)
    return edited, deleted_after


# ---------- analysis ----------


def innermost(funcs: list[Func], line: int) -> Func | None:
    inside = [f for f in funcs if f.start <= line <= f.end]
    return min(inside, key=lambda f: f.end - f.start) if inside else None


def _from(module: str, pool: list[Func]) -> list[Func]:
    """Functions defined in module, or re-exported by it as a package."""
    exact = [f for f in pool if f.module == module]
    return exact or [f for f in pool if f.module.startswith(module + "/")]


def resolve(caller: Func, name: str, receiver, by_short: dict) -> Func | None:
    """The one changed function a call means, or None when the evidence is not there."""
    imported = caller.imports.get(receiver or name)
    if receiver is None and imported and imported[1]:
        name = imported[1]
    candidates = [f for f in by_short.get(name, ()) if f.lang == caller.lang and f is not caller]
    if receiver in ("self", "cls", "this"):
        pool = [f for f in candidates if f.file == caller.file and f.cls == caller.cls]
    elif receiver is None:
        pool = [f for f in candidates if f.file == caller.file and f.cls is None]
        if not pool and imported and imported[1]:
            pool = _from(imported[0], [f for f in candidates if f.cls is None])
    elif imported:
        module = "/".join(filter(None, imported))
        pool = _from(module, [f for f in candidates if f.cls is None])
    else:
        hint = receiver.lower()
        pool = [f for f in candidates if f.cls and f.cls.lower() == hint]
    return pool[0] if len(pool) == 1 else None


def changed_functions(merge_base: str, head: str, cwd=None):
    """New or edited functions keyed by file::name, plus the files listed flat."""
    entries = changed_files(merge_base, head, cwd=cwd)
    targets: dict[str, Func] = {}
    flat: list[dict] = []
    code_files = set()
    new_sources = blobs(head, [new for status, _o, new in entries if status != "D"], cwd=cwd)
    old_sources = blobs(
        merge_base, [old for status, old, _n in entries if status not in "AD"], cwd=cwd
    )
    for status, old, new in entries:
        if status == "D":
            flat.append({"file": old, "change": "deleted"})
            continue
        change = "new" if status == "A" else "changed"
        funcs = functions(new, new_sources[new]) if new in new_sources else None
        if is_test(new) or funcs is None:
            flat.append({"file": new, "change": change})
            continue
        if status == "A":
            hit, known = {f.key for f in funcs}, set()
        else:
            known = {f.name for f in functions(old, old_sources.get(old, "")) or ()}
            edited, deleted_after = touched_lines(merge_base, head, old, new, cwd=cwd)
            hit = {f.key for line in edited if (f := innermost(funcs, line))}
            hit |= {
                f.key
                for line in deleted_after
                if (f := innermost(funcs, line)) and f is innermost(funcs, line + 1)
            }
        picked = [f for f in funcs if f.key in hit]
        for f in picked:
            f.change = "changed" if f.name in known else "new"
            targets[f.key] = f
        if picked:
            code_files.add(new)
        else:
            flat.append({"file": new, "change": change})
    return targets, flat, code_files


def entry_callers(head: str, by_short: dict, targets: dict, cwd=None) -> dict[str, Func]:
    """Unchanged entry points that call a changed function directly."""
    tracked = [
        p
        for p in git("ls-tree", "-r", "-z", "--name-only", head, cwd=cwd).split("\0")
        if p and language(p) and not is_test(p) and not SKIP_DIRS & set(p.split("/"))
    ]
    return {
        f.key: f
        for path, source in blobs(head, tracked, cwd=cwd).items()
        for f in functions(path, source) or ()
        if f.entry
        and f.key not in targets
        and any(resolve(f, name, recv, by_short) for _l, name, recv in f.calls)
    }


def call_tree(targets: dict, callers: dict, by_short: dict, max_depth: int, max_nodes: int):
    """(roots, truncated, changed functions left out) under the depth and node caps."""
    nodes = {**targets, **callers}
    edges: dict[str, list[str]] = {}
    for key, f in nodes.items():
        edges[key] = []
        for _line, name, recv in f.calls:
            callee = resolve(f, name, recv, by_short)
            if callee is not None and callee.key != key and callee.key not in edges[key]:
                edges[key].append(callee.key)
    called = {c for kids in edges.values() for c in kids}
    order = lambda k: (not edges[k], nodes[k].file, nodes[k].line)  # noqa: E731
    roots = sorted(callers, key=order) + sorted((k for k in targets if k not in called), key=order)
    reached = set()

    def reach(key):
        stack = [key]
        while stack:
            if (k := stack.pop()) not in reached:
                reached.add(k)
                stack.extend(edges[k])

    for key in roots:
        reach(key)
    while set(targets) - reached:  # functions that only call each other in a cycle
        roots.append(min(set(targets) - reached, key=order))
        reach(roots[-1])
    count, expanded, truncated = 0, set(), False

    def build(key, depth, path):
        nonlocal count, truncated
        count += 1
        f = nodes[key]
        node = {"name": f.name, "file": f.file, "line": f.line, "change": f.change}
        if f.params:
            node["params"] = f.params
        kids = [k for k in edges[key] if k not in path]
        if key in expanded and kids:
            node["seen"] = True
            return node
        expanded.add(key)
        node["children"] = []
        for i, kid in enumerate(kids):
            if depth + 1 >= max_depth or count >= max_nodes:
                node["more"], truncated = len(kids) - i, True
                break
            node["children"].append(build(kid, depth + 1, path | {kid}))
        return node

    tree = []
    for key in roots:
        if count >= max_nodes:
            truncated = True
            break
        if key not in expanded:
            tree.append(build(key, 0, {key}))
    return tree, truncated, len(set(targets) - expanded)


def analyse(base: str, head: str = "HEAD", *, cwd=None, max_depth=8, max_nodes=80) -> dict:
    merge_base = git("merge-base", base, head, cwd=cwd).strip()
    targets, flat, code_files = changed_functions(merge_base, head, cwd=cwd)
    by_short: dict[str, list[Func]] = {}
    for f in targets.values():
        by_short.setdefault(f.short, []).append(f)
    callers = entry_callers(head, by_short, targets, cwd=cwd)
    tree, truncated, hidden = call_tree(targets, callers, by_short, max_depth, max_nodes)
    result = {
        "base": base,
        "head": git("rev-parse", "--short", head, cwd=cwd).strip(),
        "stats": {
            "new": sum(f.change == "new" for f in targets.values()),
            "changed": sum(f.change == "changed" for f in targets.values()),
            "files": len(code_files) + len(flat),
        },
        "roots": tree,
        "files": flat[:200],
        "truncated": truncated or len(flat) > 200,
    }
    if hidden:
        result["more"] = hidden
    return result


# ---------- upload ----------


def upload(tree: dict, card: int, hub: str, token: str) -> dict:
    url = f"{hub.rstrip('/')}/api/cards/{card}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def call(method, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    for attempt in range(3):
        current = call("GET")
        try:
            return call("PATCH", {"version": current["version"], "callstack": tree})
        except urllib.error.HTTPError as exc:
            if exc.code != 409 or attempt == 2:
                detail = exc.read().decode("utf-8", "replace")
                raise SystemExit(f"callstack: {exc.code} {detail}") from None
    raise AssertionError("unreachable")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m commonplace.callstack")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--max-nodes", type=int, default=80)
    parser.add_argument("--card", type=lambda v: int(v.removeprefix("#")))
    parser.add_argument("--hub", default=os.environ.get("COMMONPLACE_URL", "https://cmmn.dev"))
    parser.add_argument("--token", default=os.environ.get("COMMONPLACE_TOKEN"))
    args = parser.parse_args(argv)
    try:
        tree = analyse(args.base, args.head, max_depth=args.max_depth, max_nodes=args.max_nodes)
    except subprocess.CalledProcessError as exc:
        print(f"callstack: git failed: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    if args.card is None:
        print(json.dumps(tree, indent=1, ensure_ascii=False))
        return 0
    if not args.token:
        print("callstack: set COMMONPLACE_TOKEN or pass --token", file=sys.stderr)
        return 2
    upload(tree, args.card, args.hub, args.token)
    stats = tree["stats"]
    print(f"#{args.card} {stats['new']} new, {stats['changed']} changed, {stats['files']} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
