"""PR call tree: git analysis on a fixture repo, the card field, REST and MCP."""

import http.client
import json
import sqlite3
import subprocess

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
    monkeypatch.chdir(repo)
    assert callstack.main(["--base", "main", "--head", "feature"]) == 0
    assert json.loads(capsys.readouterr().out)["stats"]["new"] == 4
    assert callstack.main(["--base", "nope"]) == 2
    monkeypatch.delenv("COMMONPLACE_TOKEN", raising=False)
    assert callstack.main(["--base", "main", "--card", "1"]) == 2
    down = ["--base", "main", "--card", "1", "--hub", "http://127.0.0.1:9", "--token", "t"]
    assert callstack.main(down) == 2
    assert "cannot reach" in capsys.readouterr().err
