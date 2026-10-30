"""A PR as a call tree: its new and changed functions, from entry point to callee.

    python -m commonplace.callstack --base origin/develop [--head HEAD]
    python -m commonplace.callstack --base origin/develop --card 142 --hub URL [--token T]

Reads ``git diff base...head`` in the current repository and prints JSON, or stores it
on a card (``callstack``); ``--card`` needs ``--hub``/``COMMONPLACE_URL`` (no default) and
refuses redirects, so the token goes to that hub only. After ``--timeout`` seconds the
files not yet read are listed flat and the tree is marked truncated. Python is parsed with ``ast``. JS/TS uses a masked-source
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
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# What the hub accepts for a card's callstack (commonplace.boards.service checks it).
LIMITS = {
    "bytes": 64_000, "name": 300, "file": 500, "params": 300, "base": 300, "head": 100,
    "nodes": 500, "depth": 16, "files": 200,
}  # fmt: skip
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

LINE = 2000  # declarations are matched on the first LINE characters of a line only
_SPECIAL = re.compile(r"[/\"'`{}]")
_TEMPLATE = re.compile(r"\\|`|\$\{")
_REGEX_BEFORE = set("(,=:[!&|?{};")


def mask_js(src: str) -> str:
    """Blank out comments, strings, regex literals and template text, keeping newlines."""
    out, n, i, templates, in_template = list(src), len(src), 0, [], False

    def blank(a, b):
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        if in_template:
            m = _TEMPLATE.search(src, i)
            if m is None:
                blank(i, n)
                break
            blank(i, m.start())
            if m[0] == "\\":
                blank(m.start(), m.start() + 2)
                i = m.start() + 2
            else:
                in_template, i = False, m.end()
                if m[0] == "${":
                    templates.append(0)
            continue
        m = _SPECIAL.search(src, i)
        if m is None:
            break
        i, c = m.start(), m[0]
        if src.startswith("//", i):
            end = src.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
        elif src.startswith("/*", i):
            end = src.find("*/", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
        elif c == "/":
            i = _regex_literal(src, out, i, blank)
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


def _regex_literal(src, out, i, blank) -> int:
    """Blank a regex literal starting at i (a "/" where an operand may start); next index."""
    k = i - 1
    while k >= 0 and out[k] in " \t\r\n":
        k -= 1
    word = "".join(out[max(0, k - 6) : k + 1])
    if not (k < 0 or out[k] in _REGEX_BEFORE or re.search(r"(?<![\w$])return$", word)):
        return i + 1
    j, in_class, limit = i + 1, False, min(len(src), i + 1000)
    while j < limit and src[j] != "\n":
        if src[j] == "\\":
            j += 2
            continue
        if src[j] == "[":
            in_class = True
        elif src[j] == "]":
            in_class = False
        elif src[j] == "/" and not in_class:
            blank(i + 1, j)
            return j + 1
        j += 1
    return i + 1


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
# A receiver only when it starts a chain: in o.store.save() neither store nor save is bound.
JS_CALL = re.compile(
    rf"(?:(?<![\w$.])(?P<recv>{_NAME})\s*(?:\?\.|\.)\s*)?(?<![\w$])(?P<name>{_NAME})\s*\("
)
JS_DEFINITION = re.compile(r"\([^()]*\)\s*(?::[^{};()]*)?\{")  # name(a) { ... } defines
JS_IMPORT = re.compile(
    rf"^[ \t]*import\s+(?:type\s+)?(?:{_NAME}\s*,?\s*)?"
    rf"(?:\{{(?P<named>[^}}]{{0,2000}})\}}|\*\s+as\s+(?P<ns>{_NAME}))?"
    r"\s*from\s*[\"'](?P<src>\.[^\"'\n]{0,500})[\"']",
    re.M,
)
_BODY_END = re.compile(r"[;\n]")
_BODY_OPEN = re.compile(r"[{;]")
_SPACE = re.compile(r"\s*")


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


class _Depth:
    """Nesting depth before any offset, from one pass over the bracket characters."""

    def __init__(self, masked: str, opens: str, closes: str):
        chars = re.escape(opens + closes)
        self.at, self.depth, depth = [], [0], 0
        for m in re.finditer(f"[{chars}]", masked):
            depth += 1 if m[0] in opens else -1
            self.at.append(m.start())
            self.depth.append(depth)

    def __call__(self, offset: int) -> int:
        return self.depth[bisect.bisect_left(self.at, offset)]


def _brace_pairs(masked: str) -> tuple[dict, bool]:
    """Closing offset for each "{" (None when unmatched), and whether all braces balance."""
    pairs, stack, balanced = {}, [], True
    for m in re.finditer(r"[{}]", masked):
        if m[0] == "{":
            stack.append(m.start())
        elif stack:
            pairs[stack.pop()] = m.start()
        else:
            balanced = False
    pairs.update(dict.fromkeys(stack))
    return pairs, balanced and not stack


def _body(masked, pos, arrow, pairs, parens, brackets):
    """(start, end) of the body declared at pos; end is None when its braces don't match."""
    if arrow:
        at = masked.find("=>", pos, pos + LINE)
        if at < 0:
            return pos, pos
        start = _SPACE.match(masked, at + 2).end()
        if masked.startswith("{", start):
            return start, pairs.get(start)
        depth, first = brackets(start), None
        for count, m in enumerate(_BODY_END.finditer(masked, start, start + 10 * LINE)):
            first = m.start() if first is None else first
            if brackets(m.start()) <= depth:
                return start, m.start()
            if count > 200:
                break  # no end in sight: claim one line, not the code after it
        if first is None:
            first = masked.find("\n", start)
        return start, len(masked) - 1 if first < 0 else first
    depth = parens(pos)
    for count, m in enumerate(_BODY_OPEN.finditer(masked, pos, pos + 10 * LINE)):
        if count > 200:
            break
        if parens(m.start()) == depth:
            return (m.start(), pairs.get(m.start())) if m[0] == "{" else (m.start(), m.start())
    return pos, pos


def _js_calls(masked: str, start: int, end: int, line_of) -> list:
    calls = []
    for call in JS_CALL.finditer(masked, start, end + 1):
        name, receiver = call["name"], call["recv"]
        if name in JS_KEYWORDS or JS_DEFINITION.match(masked, call.end() - 1):
            continue
        before = masked[max(0, call.start("name") - 12) : call.start("name")].rstrip()
        if before.endswith("function"):
            continue
        if receiver is None and before.endswith("."):
            receiver = "?"  # the tail of a longer chain: its owner is unknown
        calls.append((line_of(call.start("name")) + 1, name, receiver))
    return calls


def js_functions(path: str, source: str) -> list[Func]:
    masked = mask_js(source)
    imports = _js_imports(path, source)
    lines = masked.split("\n")
    offsets, total = [], 0
    for text in lines:
        offsets.append(total)
        total += len(text) + 1
    line_of = lambda offset: bisect.bisect_right(offsets, offset) - 1  # noqa: E731
    pairs, balanced = _brace_pairs(masked)
    opens = sorted(pairs)
    braces, parens = _Depth(masked, "{", "}"), _Depth(masked, "(", ")")
    brackets = _Depth(masked, "([{", ")]}")
    classes = []  # (name, body open, body close), in source order
    for k, text in enumerate(lines):
        if (m := JS_CLASS.match(text[:LINE])) is not None:
            i = bisect.bisect_left(opens, offsets[k] + m.end())
            if i < len(opens) and pairs[opens[i]] is not None:
                classes.append((m["name"], opens[i], pairs[opens[i]]))
    found, active, next_class, body = [], [], 0, (-1, -1)
    for k, line in enumerate(lines):
        pos, text = offsets[k], line[:LINE]
        while next_class < len(classes) and classes[next_class][1] < pos:
            while active and active[-1][2] < classes[next_class][1]:
                active.pop()
            active.append(classes[next_class])
            next_class += 1
        while active and active[-1][2] < pos:
            active.pop()
        owner = active[-1] if active else None
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
            and braces(pos) == braces(owner[1]) + 1
        ):
            cls, params = owner[0], m["params"]
        else:
            continue
        if (owner and not cls) or body[0] < pos <= body[1]:
            continue  # a local helper folds into the function around it
        start, end = _body(masked, pos + m.start("name"), arrow, pairs, parens, brackets)
        trusted = balanced and end is not None  # unbalanced braces: calls can't be placed
        end = len(masked) - 1 if end is None else end
        body = (start, end)
        name = m["name"]
        found.append(
            Func(
                file=path,
                name=f"{cls}.{name}" if cls else name,
                line=k + 1,
                start=k + 1,
                end=max(k + 1, line_of(end) + 1),
                params=_clip(params),
                lang="js",
                cls=cls,
                entry=exported or bool(ENTRY_NAME.match(name)),
                calls=_js_calls(masked, start, end, line_of) if trusted else [],
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


def git(*args, cwd=None, ok=(0,)) -> str:
    """Paths stay byte-exact through surrogateescape; see shown() for display."""
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    if proc.returncode not in ok:
        stderr = proc.stderr.decode("utf-8", "replace")
        raise subprocess.CalledProcessError(proc.returncode, ["git", *args], proc.stdout, stderr)
    return proc.stdout.decode("utf-8", "surrogateescape")


def shown(path: str) -> str:
    return path.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def blobs(rev: str, paths, cwd=None) -> dict[str, str]:
    paths = [p for p in paths if "\n" not in p]
    if not paths:
        return {}
    request = "".join(f"{rev}:{p}\n" for p in paths).encode("utf-8", "surrogateescape")
    data = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=cwd, check=True, capture_output=True, input=request
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


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)
PATCH = re.compile(r"^diff --git ", re.M)


def file_changes(merge_base: str, head: str, cwd=None) -> list[tuple]:
    """(status, old path, new path, edited lines, lines followed by a deletion) per file.

    One diff-tree run: no pathspecs (so names like [x].py are literal), no external diff
    or textconv drivers. The raw -z records give exact paths and come in the same order
    as the patch sections, so headers never need unquoting.
    """
    out = git(
        "diff-tree", "-r", "-M", "-z", "--patch-with-raw", "-U0", "--no-ext-diff",
        "--no-textconv", merge_base, head, cwd=cwd,
    )  # fmt: skip
    fields, records, i = out.split("\0"), [], 0
    while i < len(fields) and fields[i].startswith(":"):
        status = fields[i].split()[-1][0]
        if status in "RC":
            records.append((status, fields[i + 1], fields[i + 2]))
            i += 3
        else:
            records.append((status, fields[i + 1], fields[i + 1]))
            i += 2
    sections = PATCH.split("\0".join(fields[i:]).lstrip("\0"))[1:]
    if len(sections) != len(records):
        sections = [""] * len(records)  # unexpected shape: no line attribution
    result = []
    for (status, old, new), patch in zip(records, sections):
        edited, deleted_after = set(), set()
        for m in HUNK.finditer(patch):
            start, count = int(m[1]), int(m[2] if m[2] is not None else 1)
            if count:
                edited.update(range(start, start + count))
            else:
                deleted_after.add(start)
        result.append((status, old, new, edited, deleted_after))
    return result


# ---------- analysis ----------


def owners(funcs: list[Func]) -> dict[int, Func]:
    """The innermost function on each line."""
    out = {}
    for f in sorted(funcs, key=lambda f: f.end - f.start, reverse=True):
        out.update(dict.fromkeys(range(f.start, f.end + 1), f))
    return out


def resolve(caller: Func, name: str, receiver, index) -> Func | None:
    """The one changed function a call means, or None when the evidence is not there."""
    by_short, reexports = index

    def defined(module, symbol, attr=None):
        """Function symbol in module, or method attr of class symbol, through re-exports."""
        for _ in range(4):
            pool = [
                f
                for f in by_short.get(attr or symbol, ())
                if f.lang == caller.lang
                and f.module == module
                and f.cls == (symbol if attr else None)
            ]
            if pool or (module, symbol) not in reexports:
                return pool
            module, symbol = reexports[(module, symbol)]
        return []

    same_file = [f for f in by_short.get(name, ()) if f.file == caller.file and f is not caller]
    imported = caller.imports.get(receiver or name)
    if receiver in ("self", "cls", "this"):
        pool = [f for f in same_file if f.cls and f.cls == caller.cls]
    elif receiver is None:
        pool = [f for f in same_file if f.cls is None]
        if not pool and imported and imported[1]:
            pool = defined(*imported)
    elif imported:
        module, symbol = imported
        pool = defined(f"{module}/{symbol}" if symbol else module, name)
        if not pool and symbol:
            pool = defined(module, symbol, name)  # an imported class: Class.method()
    else:
        pool = [f for f in same_file if f.cls == receiver]  # a class from this file
    return pool[0] if len(pool) == 1 else None


def changed_functions(merge_base: str, head: str, deadline: float, cwd=None):
    """New or edited functions keyed by file::name, the files listed flat, and whether
    the deadline cut the scan short."""
    entries = file_changes(merge_base, head, cwd=cwd)
    targets: dict[str, Func] = {}
    flat: list[dict] = []
    code_files, late = set(), False
    new_sources = blobs(head, [e[2] for e in entries if e[0] != "D"], cwd=cwd)
    old_sources = blobs(merge_base, [e[1] for e in entries if e[0] not in "AD"], cwd=cwd)
    for status, old, new, edited, deleted_after in entries:
        if status == "D":
            flat.append({"file": old, "change": "deleted"})
            continue
        change = "new" if status == "A" else "changed"
        late = late or time.monotonic() > deadline
        funcs = None
        if not late and not is_test(new) and new in new_sources:
            funcs = functions(new, new_sources[new])
        if funcs is None:
            flat.append({"file": new, "change": change})
            continue
        if status == "A":
            hit, known = {f.key for f in funcs}, set()
        else:
            known = {f.name for f in functions(old, old_sources.get(old, "")) or ()}
            by_line = owners(funcs)
            hit = {by_line[line].key for line in edited if line in by_line}
            hit |= {
                by_line[line].key
                for line in deleted_after
                if line in by_line and by_line[line] is by_line.get(line + 1)
            }
        picked = [f for f in funcs if f.key in hit]
        for f in picked:
            f.change = "changed" if f.name in known else "new"
            targets[f.key] = f
        if picked:
            code_files.add(new)
        else:
            flat.append({"file": new, "change": change})
    return targets, flat, code_files, late


def reexports(targets: dict, head: str, cwd=None) -> dict:
    """(package, name) -> (module, name) for what a package __init__.py imports and so
    re-exports, for the packages around changed Python functions."""
    packages = {
        "/".join(parts[:i])
        for f in targets.values()
        if f.lang == "py"
        for parts in [f.module.split("/")]
        for i in range(1, len(parts) + 1)
    }
    out = {}
    for path, source in blobs(head, [f"{p}/__init__.py" for p in sorted(packages)], cwd).items():
        try:
            imports = _py_imports(path, ast.parse(source))
        except (SyntaxError, ValueError, RecursionError):
            continue
        for local, (module, name) in imports.items():
            if name:
                out[(module_of(path), local)] = (module, name)
    return out


def entry_callers(head: str, index, targets: dict, deadline: float, cwd=None):
    """Unchanged entry points that call a changed function directly, read only from files
    that mention a changed name; and whether the deadline cut the scan short."""
    names = sorted({f.short for f in targets.values()})
    if not names:
        return {}, False
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt") as patterns:
        patterns.write("\n".join(names) + "\n")
        patterns.flush()
        found = git(
            "grep", "-l", "-z", "-I", "-w", "-F", "-f", patterns.name, head, cwd=cwd, ok=(0, 1)
        )
    prefix = f"{head}:"
    listed = [p[len(prefix) :] for p in found.split("\0") if p.startswith(prefix)]
    paths = [
        p for p in listed if language(p) and not is_test(p) and not SKIP_DIRS & set(p.split("/"))
    ]
    callers = {}
    for i in range(0, len(paths), 100):
        if time.monotonic() > deadline:
            return callers, True
        for path, source in blobs(head, paths[i : i + 100], cwd=cwd).items():
            for f in functions(path, source) or ():
                if f.entry and f.key not in targets:
                    if any(resolve(f, name, recv, index) for _l, name, recv in f.calls):
                        callers[f.key] = f
    return callers, False


def call_tree(targets: dict, callers: dict, index, max_depth: int, max_nodes: int):
    """(roots, truncated, changed functions left out) under the depth and node caps."""
    nodes = {**targets, **callers}
    edges: dict[str, list[str]] = {}
    for key, f in nodes.items():
        edges[key] = []
        for _line, name, recv in f.calls:
            callee = resolve(f, name, recv, index)
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
        node = {
            "name": _clip(f.name, LIMITS["name"]),
            "file": _tail(shown(f.file), LIMITS["file"]),
            "line": f.line,
            "change": f.change,
        }
        if f.params:
            node["params"] = _clip(f.params, LIMITS["params"])
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


def _tail(path: str, limit: int) -> str:
    return path if len(path) <= limit else "…" + path[-(limit - 1) :]


def _size(result: dict) -> int:
    return len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode())


def _changed(nodes) -> set:
    out = set()
    for node in nodes:
        if node["change"] != "unchanged":
            out.add((node["file"], node["name"]))
        out |= _changed(node.get("children", ()))
    return out


def fit(result: dict) -> dict:
    """Drop flat files, then whole trees from the end, until the hub's size limit holds."""
    while _size(result) > LIMITS["bytes"] and (result["files"] or result["roots"]):
        result["truncated"] = True
        if result["files"]:
            del result["files"][-max(1, len(result["files"]) // 4) :]
            continue
        dropped = _changed(result["roots"][-1:]) - _changed(result["roots"][:-1])
        result["roots"].pop()
        result["more"] = result.get("more", 0) + len(dropped)
    return result


def analyse(
    base: str, head: str = "HEAD", *, cwd=None, max_depth=8, max_nodes=80, timeout=20.0
) -> dict:
    deadline = time.monotonic() + timeout
    top = git("rev-parse", "--show-toplevel", cwd=cwd).strip()
    merge_base = git("merge-base", base, head, cwd=top).strip()
    targets, flat, code_files, late = changed_functions(merge_base, head, deadline, cwd=top)
    by_short: dict[str, list[Func]] = {}
    for f in targets.values():
        by_short.setdefault(f.short, []).append(f)
    index = (by_short, reexports(targets, head, cwd=top))
    callers, cut = entry_callers(head, index, targets, deadline, cwd=top)
    max_depth = max(1, min(max_depth, LIMITS["depth"]))
    max_nodes = max(1, min(max_nodes, LIMITS["nodes"]))
    tree, truncated, hidden = call_tree(targets, callers, index, max_depth, max_nodes)
    files = [{"file": _tail(shown(f["file"]), LIMITS["file"]), "change": f["change"]} for f in flat]
    result = {
        "base": _clip(base, LIMITS["base"]),
        "head": git("rev-parse", "--short", head, cwd=top).strip()[: LIMITS["head"]],
        "stats": {
            "new": sum(f.change == "new" for f in targets.values()),
            "changed": sum(f.change == "changed" for f in targets.values()),
            "files": len(code_files) + len(flat),
        },
        "roots": tree,
        "files": files[: LIMITS["files"]],
        "truncated": truncated or late or cut or len(files) > LIMITS["files"],
    }
    if hidden:
        result["more"] = hidden
    return fit(result)


# ---------- upload ----------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The token is for the hub named on the command line, nowhere else."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError(f"refused redirect to {newurl}")


def upload(tree: dict, card: int, hub: str, token: str) -> dict:
    if urllib.parse.urlsplit(hub).scheme not in ("http", "https"):
        raise ValueError("hub must be an http(s) URL")
    url = f"{hub.rstrip('/')}/api/cards/{card}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    opener = urllib.request.build_opener(_NoRedirect)

    def call(method, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with opener.open(request, timeout=30) as response:
            return json.loads(response.read())

    for attempt in range(3):  # a 409 means the card moved on between read and write
        try:
            current = call("GET")
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
    parser.add_argument("--timeout", type=float, default=20.0, help="seconds; then list flat")
    parser.add_argument("--card", type=lambda v: int(v.removeprefix("#")))
    parser.add_argument("--hub", default=os.environ.get("COMMONPLACE_URL"))
    parser.add_argument("--token", default=os.environ.get("COMMONPLACE_TOKEN"))
    args = parser.parse_args(argv)
    if args.card is not None and not (args.hub and args.token):
        need = "--card needs --hub or COMMONPLACE_URL, and --token or COMMONPLACE_TOKEN"
        print(f"callstack: {need}", file=sys.stderr)
        return 2
    try:
        tree = analyse(
            args.base,
            args.head,
            max_depth=args.max_depth,
            max_nodes=args.max_nodes,
            timeout=args.timeout,
        )
    except subprocess.CalledProcessError as exc:
        print(f"callstack: git failed: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    if args.card is None:
        print(json.dumps(tree, indent=1, ensure_ascii=False))
        return 0
    try:
        upload(tree, args.card, args.hub, args.token)
    except urllib.error.URLError as exc:
        print(f"callstack: {args.hub}: {exc.reason}", file=sys.stderr)
        return 2
    except (ValueError, TimeoutError, OSError) as exc:
        print(f"callstack: {args.hub}: {exc}", file=sys.stderr)
        return 2
    stats = tree["stats"]
    print(f"#{args.card} {stats['new']} new, {stats['changed']} changed, {stats['files']} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
