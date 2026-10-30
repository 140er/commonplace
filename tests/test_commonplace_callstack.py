"""PR call tree: git analysis on a fixture repo, the card field, REST and MCP."""

import http.client
import http.server
import json
import sqlite3
import subprocess
import threading
import time

import pytest

from commonplace import callstack, sessions
from commonplace.boards.service import BoardService, callstack_json
from commonplace.boards.store import BoardStore
from commonplace.extensions import ToolContext, ToolError
from tests._commonplace_helpers import hub_fixture  # noqa: F401

BASE = {
    "app.py": """\
def helper(x):
    return x + 1


def trimmed():
    a = 1
    b = 2
    return a


def untouched():
    return 0


def main():
    return helper(2)


class Store:
    def save(self, value):
        return value
""",
    "other.py": """\
def main():
    return helper(3)
""",
    "web/app.js": """\
export function render(card) {
  return draw(card);
}

function draw(card) {
  return `<p>${card}</p>`;
}
""",
    "notes.txt": "one\n",
}

HEAD = {
    "app.py": """\
def helper(x):
    return fresh(x) + 1


def fresh(x):
    return x * 2


def trimmed():
    a = 1
    return a


def untouched():
    return 0


def main():
    return helper(2)


class Store:
    def save(self, value):
        return self.persist(value)

    def persist(self, value):
        return value
""",
    "cli/cmd_go.py": """\
from app import fresh


def cmd_go(args):
    return fresh(args)
""",
    "web/app.js": """\
export function render(card) {
  return draw(card);
}

function draw(card) {
  const close = `${"}"}`;
  return badge(card) + close;
}

const badge = (card) => {
  return `<b>${card}</b>`;
};
""",
    "tests/test_app.py": "def test_fresh():\n    assert True\n",
    "notes.txt": "one\ntwo\n",
}


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def write(repo, files):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    write(repo, BASE)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "feature")
    write(repo, HEAD)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "feature")
    return repo


def flatten(nodes, depth=0):
    for node in nodes:
        yield depth, node
        yield from flatten(node.get("children", []), depth + 1)


def shape(nodes):
    return [(depth, n["name"], n["change"]) for depth, n in flatten(nodes)]


def test_tree_from_fixture_repo(repo):
    result = callstack.analyse("main", "feature", cwd=repo)
    assert result["stats"] == {"new": 4, "changed": 4, "files": 5}
    assert result["truncated"] is False and "more" not in result
    tree = shape(result["roots"])
    # Unchanged entry points lead; a changed function's new callee nests under it.
    assert tree[:3] == [(0, "main", "unchanged"), (1, "helper", "changed"), (2, "fresh", "new")]
    assert (0, "cmd_go", "new") in tree and tree[tree.index((0, "cmd_go", "new")) + 1] == (
        1,
        "fresh",
        "new",
    )
    assert [t for t in tree if t[1].startswith("Store.")] == [
        (0, "Store.save", "changed"),
        (1, "Store.persist", "new"),
    ]
    assert (0, "trimmed", "changed") in tree  # a deletion inside the body counts
    assert [t for t in tree if t[1] in ("render", "draw", "badge")] == [
        (0, "render", "unchanged"),
        (1, "draw", "changed"),
        (2, "badge", "new"),
    ]
    names = {n["name"] for _d, n in flatten(result["roots"])}
    assert "untouched" not in names
    # other.py's main calls helper without importing it: no evidence, no edge.
    assert [n["file"] for _d, n in flatten(result["roots"]) if n["name"] == "main"] == ["app.py"]
    node = next(n for _d, n in flatten(result["roots"]) if n["name"] == "fresh")
    assert node["file"] == "app.py" and node["line"] == 5 and node["params"] == "x"
    assert {f["file"]: f["change"] for f in result["files"]} == {
        "notes.txt": "changed",
        "tests/test_app.py": "new",
    }
    assert callstack_json(result)


def test_caps_mark_truncation(repo):
    shallow = callstack.analyse("main", "feature", cwd=repo, max_depth=1)
    assert shallow["truncated"] is True
    assert all(not n.get("children") for n in shallow["roots"])
    assert any(n.get("more") for n in shallow["roots"])
    small = callstack.analyse("main", "feature", cwd=repo, max_nodes=2)
    assert small["truncated"] is True and small["more"] > 0
    assert len(list(flatten(small["roots"]))) == 2


def test_js_masking_and_python_parse_errors():
    masked = callstack.mask_js('const a = "{"; // }\nlet b = `x${ {y: 1}.y }`; /* { */\n')
    assert masked.count("{") == masked.count("}") == 2
    funcs = callstack.js_functions(
        "a.ts",
        "class Box {\n  open(x: number): void {\n    this.shut();\n  }\n  shut() {}\n}\n",
    )
    assert [(f.name, f.line, f.end) for f in funcs] == [("Box.open", 2, 4), ("Box.shut", 5, 5)]
    assert funcs[0].calls == [(3, "shut", "this")]
    assert callstack.functions("broken.py", "def (:\n") is None
    assert callstack.functions("style.css", "a {}") is None
    assert callstack.is_test("pkg/__tests__/x.js") and callstack.is_test("a/b.spec.ts")


def test_validation():
    node = {"name": "a", "file": "a.py", "line": 1, "change": "new", "children": []}
    stored = callstack_json({"roots": [node], "stats": {"new": 1}, "files": []})
    assert json.loads(stored)["roots"][0]["name"] == "a"
    assert callstack_json(None) is None
    bad = [
        [],
        {"roots": [{**node, "change": "gone"}]},
        {"roots": [{**node, "extra": 1}]},
        {"roots": [{**node, "name": ""}]},
        {"roots": [{**node, "line": "1"}]},
        {"roots": [node], "secret": 1},
        {"files": [{"file": "x", "change": "renamed"}]},
        {"stats": {"new": -1}},
    ]
    deep = dict(node)
    for _ in range(20):
        deep = {**node, "children": [deep]}
    bad.append({"roots": [deep]})
    for value in bad:
        with pytest.raises(ToolError, match="invalid callstack"):
            callstack_json(value)
    wide = {"roots": [{**node, "name": "x" * 300} for _ in range(300)]}
    with pytest.raises(ToolError, match="too large"):
        callstack_json(wide)


def test_existing_database_gains_the_column(tmp_path):
    BoardStore(tmp_path)
    with sqlite3.connect(tmp_path / "boards.sqlite3") as conn:
        conn.execute("ALTER TABLE tasks DROP COLUMN callstack")
    BoardStore(tmp_path)
    with sqlite3.connect(tmp_path / "boards.sqlite3") as conn:
        assert "callstack" in {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}


def api(hub, token, method, path, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=10)
    try:
        connection.request(
            method,
            path,
            json.dumps(body).encode() if body is not None else None,
            {"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def mcp(hub, token, name, **args):
    response = hub.rpc(token, "tools/call", {"name": name, "arguments": args})
    result = response["result"]
    return not result.get("isError", False), result["content"][0]["text"]


def test_rest_mcp_round_trip_and_upload(hub, repo, monkeypatch):
    token = hub.owner_token("agent")
    person = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    BoardService(ToolContext(principal=person, gateway=hub.gateway)).board_add(slug="work")
    status, card = api(hub, token, "POST", "/api/boards/work/cards", {"title": "Ship"})
    number = card["number"]
    assert status == 201 and mcp(hub, token, "task_take_next", task=number)[0]
    tree = callstack.analyse("main", "feature", cwd=repo)

    status, updated = api(
        hub,
        token,
        "PATCH",
        f"/api/cards/{number}",
        {"version": api(hub, token, "GET", f"/api/cards/{number}")[1]["version"], "pr": 7},
    )
    assert status == 200 and "callstack" not in updated
    status, updated = api(
        hub,
        token,
        "PATCH",
        f"/api/cards/{number}",
        {"version": updated["version"], "callstack": tree},
    )
    assert status == 200
    assert api(hub, token, "GET", f"/api/cards/{number}")[1]["callstack"] == tree
    listed = api(hub, token, "GET", "/api/boards/work/cards")[1]["cards"]
    assert "callstack" not in listed[0]
    status, error = api(
        hub,
        token,
        "PATCH",
        f"/api/cards/{number}",
        {"version": updated["version"], "callstack": {"roots": "no"}},
    )
    assert status == 403 and error["error"] == "invalid callstack"

    ok, _ = mcp(hub, token, "task_update", task=number, callstack={"roots": [], "stats": {}})
    assert ok
    assert api(hub, token, "GET", f"/api/cards/{number}")[1]["callstack"]["roots"] == []

    # The agent that asked for review may refresh the tree, and nothing else.
    assert mcp(hub, token, "task_handoff", task=number, to="owner", note="Merge?")[0]
    monkeypatch.chdir(repo)
    hub_url = f"http://127.0.0.1:{hub.port}"
    args = ["--base", "main", "--head", "feature", "--card", f"#{number}", "--hub", hub_url]
    assert callstack.main([*args, "--token", token]) == 0
    ok, shown = mcp(hub, token, "task_show", task=number)
    assert ok and "call tree: 4 new, 4 changed, 5 files" in shown
    assert '- changed "helper" "app.py:1"' in shown
    ok, refused = mcp(hub, token, "task_update", task=number, checks="passing", callstack=None)
    assert not ok and "waiting on a person" in refused
    assert mcp(hub, token, "task_update", task=number, callstack=None)[0]
    assert api(hub, token, "GET", f"/api/cards/{number}")[1]["callstack"] is None


def test_cli_prints_json_and_reports_errors(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo / "web")  # a subdirectory: git runs from the top level
    assert callstack.main(["--base", "main", "--head", "feature"]) == 0
    assert json.loads(capsys.readouterr().out)["stats"]["new"] == 4
    assert callstack.main(["--base", "nope"]) == 2
    monkeypatch.delenv("COMMONPLACE_TOKEN", raising=False)
    monkeypatch.delenv("COMMONPLACE_URL", raising=False)
    assert callstack.main(["--base", "main", "--card", "1", "--token", "t"]) == 2
    assert "needs --hub" in capsys.readouterr().err  # no default hub
    down = ["--base", "main", "--card", "1", "--token", "t", "--hub"]
    assert callstack.main([*down, "http://127.0.0.1:9"]) == 2
    assert callstack.main([*down, "ftp://127.0.0.1/x"]) == 2
    monkeypatch.setattr(callstack, "upload", lambda *a: (_ for _ in ()).throw(TimeoutError()))
    assert callstack.main([*down, "http://127.0.0.1:9"]) == 2
    err = capsys.readouterr().err
    assert "Connection refused" in err and "http(s)" in err and "Traceback" not in err


def serve(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_upload_refuses_redirects(repo, monkeypatch, capsys):
    seen = []

    class Elsewhere(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"version": 1}')

        def log_message(self, *args):
            pass

    other = serve(Elsewhere)

    class Hub(Elsewhere):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{other.server_port}/steal")
            self.end_headers()

    class Garbled(Elsewhere):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html>")

    hub, garbled = serve(Hub), serve(Garbled)
    try:
        monkeypatch.chdir(repo)
        args = ["--base", "main", "--head", "feature", "--card", "1", "--token", "secret"]
        assert callstack.main([*args, "--hub", f"http://127.0.0.1:{hub.server_port}"]) == 2
        assert "refused redirect" in capsys.readouterr().err and seen == []
        assert callstack.main([*args, "--hub", f"http://127.0.0.1:{garbled.server_port}"]) == 2
        assert "Traceback" not in capsys.readouterr().err
    finally:
        for server in (hub, garbled, other):
            server.shutdown()


def build(tmp_path, base, head, name="r"):
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    write(repo, base)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base", "--allow-empty")
    git(repo, "checkout", "-qb", "feature")
    write(repo, head)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "feature")
    return repo


def edges(result):
    out = set()
    for _depth, node in flatten(result["roots"]):
        out |= {(node["name"], child["name"]) for child in node.get("children", [])}
    return out


def test_no_invented_edges(tmp_path):
    head = {
        "web/chain.js": """\
function save(x) {
  return x;
}

export function run(o) {
  return o.store.save(1) + o?.save(2);
}

function render(y) {
  return y;
}

function make() {
  return { render(y) { return y; } };
}

function scan(text) {
  const r = /[{]+\\/}/g;
  return pick(text.match(r));
}

function pick(m) {
  return m;
}
""",
        "parse.py": """\
class Parser:
    def parse(self):
        return 1


def go(parser):
    return parser.parse()


def direct():
    return Parser.parse(None)
""",
        "pkg/__init__.py": "",
        "pkg/core.py": "def search():\n    return 1\n",
        "pkg2/__init__.py": "from .core import find\n",
        "pkg2/core.py": "def find():\n    return 2\n",
        "use.py": """\
from pkg import search
from pkg2 import find


def both():
    return search() + find()
""",
    }
    result = callstack.analyse("main", "feature", cwd=build(tmp_path, {"x.txt": "x"}, head))
    found = edges(result)
    assert ("run", "save") not in found  # o.store.save(), o?.save(): receivers unknown
    assert ("make", "render") not in found  # an object-literal method definition
    assert ("scan", "pick") in found  # braces inside the regex literal stay masked
    assert ("go", "Parser.parse") not in found  # a variable named like the class
    assert ("direct", "Parser.parse") in found  # the class itself, from this file
    assert ("both", "search") not in found  # pkg/__init__.py does not re-export it
    assert ("both", "find") in found  # pkg2/__init__.py does


def test_unbalanced_braces_drop_calls():
    funcs = callstack.js_functions("a.js", "function a() {\n  b();\n\nfunction b() {}\n")
    assert [f.name for f in funcs] == ["a"] and funcs[0].calls == []
    masked = callstack.mask_js("x = /}/.test(y) ? a / b : c;\nreturn /{/;\n")
    assert "{" not in masked and "}" not in masked and "a / b" in masked


def test_hostile_file_names(tmp_path):
    base = {"x.py": "def plain():\n    return 0\n", "[x].py": "def odd():\n    return 0\n"}
    head = {
        "x.py": base["x.py"],
        "[x].py": "def odd():\n    return 1\n",
        'sp ace "q" é.py': "def spaced():\n    return 2\n",
    }
    repo = build(tmp_path, base, head)
    git(repo, "config", "diff.external", "false")
    result = callstack.analyse("main", "feature", cwd=repo)
    assert shape(result["roots"]) == [(0, "odd", "changed"), (0, "spaced", "new")]
    assert {n["file"] for _d, n in flatten(result["roots"])} == {"[x].py", 'sp ace "q" é.py'}


def test_entry_scan_reads_only_files_naming_a_change(tmp_path, monkeypatch):
    base = {f"lib/m{i}.py": f"def f{i}():\n    return {i}\n" for i in range(150)}
    base["app.py"] = "def target():\n    return 0\n"
    base["cli.py"] = "from app import target\n\n\ndef main():\n    return target()\n"
    head = {"app.py": "def target():\n    return 1\n"}
    repo = build(tmp_path, base, head)
    parsed = []
    original = callstack.functions
    monkeypatch.setattr(
        callstack, "functions", lambda path, source: parsed.append(path) or original(path, source)
    )
    result = callstack.analyse("main", "feature", cwd=repo)
    assert shape(result["roots"]) == [(0, "main", "unchanged"), (1, "target", "changed")]
    assert not [p for p in parsed if p.startswith("lib/")]


PATHOLOGICAL = (
    "import {" + " a," * 20000 + "\n"
    + "class A\n" * 4000
    + "function f(\n" * 4000
    + "const g = (x) =>\n" * 4000
    + "(/[" + " /" * 20000 + "\n"
    + "q" * 100000 + "\n"
    + "export function h() {\n" + "  o.p.q(r(s(t())));\n" * 3000 + "}\n"
)  # fmt: skip


def test_pathological_source_is_fast(tmp_path):
    started = time.monotonic()
    funcs = callstack.js_functions("evil.js", PATHOLOGICAL)
    assert time.monotonic() - started < 5
    assert "h" in [f.name for f in funcs]
    repo = build(tmp_path, {"a.js": "export const a = 1;\n"}, {"evil.js": PATHOLOGICAL})
    started = time.monotonic()
    result = callstack.analyse("main", "feature", cwd=repo)
    assert time.monotonic() - started < 10
    assert callstack_json(result)


def test_deadline_lists_files_flat(repo):
    result = callstack.analyse("main", "feature", cwd=repo, timeout=0)
    assert result["truncated"] is True and result["roots"] == []
    assert {f["file"] for f in result["files"]} >= {"app.py", "web/app.js", "cli/cmd_go.py"}


def test_output_always_fits_the_hub():
    node = {"name": "n" * 300, "file": "f" * 500, "line": 1, "change": "new", "params": "p" * 80}
    result = {
        "base": "main",
        "head": "abc",
        "stats": {"new": 400, "changed": 0, "files": 300},
        "roots": [{**node, "name": f"{i}" + "n" * 290, "children": []} for i in range(400)],
        "files": [{"file": f"{i}" + "x" * 490, "change": "changed"} for i in range(200)],
        "truncated": False,
    }
    fitted = callstack.fit(result)
    assert fitted["truncated"] is True and fitted["files"] == []
    assert fitted["more"] == 400 - len(fitted["roots"]) > 0
    assert len(callstack_json(fitted).encode()) <= callstack.LIMITS["bytes"]
