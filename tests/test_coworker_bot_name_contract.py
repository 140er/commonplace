"""Shared human-name validation agrees with the actual bot stores."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from commonplace.bots.store import BotStore, Invalid
from commonplace.chat.coworkers.store import Store, StoreError


@pytest.mark.parametrize("personal", [False, True], ids=["conversation", "personal"])
@pytest.mark.parametrize(
    "name",
    [
        "Research Helper",
        "Zoë 李",
        "مساعد الفريق",
        "Bad/Name",
        ":lead",
        "!!!",
        "😀" * 60,
        "😀" * 61,
        "é" * 60,
        "é" * 61,
        "漢" * 80,
        "漢" * 81,
        "",
        "   ",
        "\ud800",
    ],
    ids=[
        "spaces",
        "unicode",
        "rtl",
        "slash",
        "colon",
        "punctuation",
        "emoji-60",
        "emoji-61",
        "accent-60",
        "accent-61",
        "cjk-80",
        "cjk-81",
        "empty",
        "blank",
        "invalid-utf8",
    ],
)
def test_client_name_rule_matches_server(tmp_path, personal, name):
    # Controllers trim the submitted display name; neither registry derives an identifier.
    submitted = name.strip()
    accepted = True
    if personal:
        supervisor = SimpleNamespace(workspace_dir=lambda _workspace: str(tmp_path))
        store = BotStore(supervisor, "fixture")
        try:
            try:
                created = store.create("owner", submitted)
            except Invalid:
                accepted = False
            else:
                assert created["name"] == submitted
        finally:
            store.close()
    else:
        store = Store(tmp_path / "conversation.sqlite3")
        try:
            created = store.create_bot("owner", submitted)
        except StoreError:
            accepted = False
        else:
            assert created["name"] == submitted

    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {botNameError} from './commonplace/web/static/bot-create.js';"
            "let input='';for await(const chunk of process.stdin) input+=chunk;"
            "const {name,personal}=JSON.parse(input);"
            "console.log(JSON.stringify({error:botNameError(name,{personal})}));",
        ],
        input=json.dumps({"name": name, "personal": personal}),
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    error = json.loads(result.stdout)["error"]
    assert (not error) == accepted, f"client error {error!r}; server accepted={accepted}"
