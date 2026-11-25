"""Round-three preview regressions: containment, safe refusals and useful quota recovery."""

import html
import json
import re
import subprocess
from pathlib import Path

import pytest

from commonplace.previews import bundle, store
from tests._console_helpers import console_hub_fixture  # noqa: F401
from tests.test_previews import card, one, prow, publish
from tests.test_previews_serving import fetch

ROOT = Path(__file__).resolve().parents[1]
VALUE = "ExampleOnly" + "1234567890" * 3
SHAPES = [
    ("assignment", f'const API_KEY = "{VALUE}";'),
    ("assignment", f"CLIENT_SECRET={VALUE}"),
    ("json_secret", json.dumps({"apiKey": VALUE})),
    ("jwt", "eyJ" + "a" * 20 + "." + "b" * 20 + "." + "c" * 20),
    ("bearer", "Authorization: Bearer " + VALUE),
]


def test_open_uses_a_viewer_and_files_cannot_be_opened_as_the_tab(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    identity = one(h, number)["id"]
    status, headers, _ = fetch(h, h.owner, f"/p/{identity}/")
    assert status == 303
    keyed = headers["Location"]
    status, headers, body = fetch(h, h.owner, keyed)
    assert status == 200
    frame = re.search(rb"<iframe\b([^>]+)>", body)
    assert frame, "the published code must run in a frame inside the viewer"
    assert b'sandbox="allow-scripts"' in frame[1]
    assert not any(
        flag in frame[1]
        for flag in (b"allow-top-navigation", b"allow-popups", b"allow-same-origin")
    )
    assert "frame-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"
    src = html.unescape(re.search(rb'src="([^"]+)"', frame[1])[1].decode())
    status, headers, body = fetch(h, h.owner, src, headers={"Sec-Fetch-Dest": "iframe"})
    assert status == 200 and body == b"<h1>Hello</h1>"
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    status, headers, _ = fetch(h, h.owner, src, headers={"Sec-Fetch-Dest": "document"})
    assert status == 303 and headers["Location"] == keyed


@pytest.mark.parametrize("kind,text", SHAPES, ids=[f"shape-{n}" for n in range(len(SHAPES))])
def test_secret_shapes_are_refused_before_storage(console_hub, kind, text):
    h = console_hub
    number = card(h)
    ok, answer = publish(h, h.t1, number, files=[{"path": "index.html", "text": text}])
    assert not ok, "credential-shaped content must be refused"
    assert "[contains_secret_like_text]" in answer and f"kind={kind}" in answer
    assert text not in answer and VALUE not in answer
    shown = one(h, number)
    assert shown["failure"] == {"file": "index.html", "kind": kind}
    assert not store.revision_dir(h.gateway, h.workspace, shown["id"], 1).exists()
    assert text not in " ".join(str(v) for v in tuple(prow(h, shown["id"])))


@pytest.mark.parametrize(
    "prefix", ["/", "hidden/.", "wrong\\"], ids=["absolute", "hidden", "backslash"]
)
def test_an_invalid_credential_filename_is_never_echoed(console_hub, prefix):
    h = console_hub
    number = card(h)
    filename = prefix + "ghp_" + "0" * 36 + ".js"
    ok, answer = publish(
        h,
        h.t1,
        number,
        files=[{"path": "index.html", "text": "x"}, {"path": filename, "text": "x"}],
    )
    assert not ok
    assert "[contains_secret_like_text]" in answer and "kind=github_token" in answer
    assert "file=" not in answer and filename not in answer and "ghp_" not in answer
    shown = one(h, number)
    assert shown["reason"] == "contains_secret_like_text"
    assert "ghp_" not in " ".join(str(v) for v in tuple(prow(h, shown["id"])))


def test_build_identifiers_and_example_settings_are_still_publishable():
    text = (
        'const tokenizer = "ExampleOnly1234567890"; const apiKey = "YOUR_API_KEY"; '
        'const secret = "a descriptive sentence"; const tokens = "ExampleOnly1234567890"; '
        'const style = {color: "red", fontFamily: "system-ui"};'
    )
    assert bundle.parse([{"path": "index.html", "text": text}]) == [("index.html", text.encode())]


@pytest.mark.parametrize(
    "limit,reason",
    [("MAX_WORKSPACE_BYTES", "workspace_space_full"), ("MAX_ROWS", "too_many_previews")],
)
def test_full_storage_points_to_the_owners_available_delete_action(
    console_hub, monkeypatch, limit, reason
):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    monkeypatch.setattr(store, limit, 1)
    ok, answer = publish(h, h.t1, number, name="second")
    assert not ok and f"[{reason}]" in answer
    shown = one(h, number, "second")
    for words in (answer, shown["next"]):
        assert "workspace owner" in words and "Delete files" in words and "on its card" in words
        assert "not available yet" not in words
        assert ("does not free space" in words) == (reason == "workspace_space_full")


def rendered_rows():
    script = """
import {cardPreviewsHTML} from './commonplace/web/static/cardpreviews.js';
const preview = {id:'pv_0123456789abcdef', name:'Report', state:'ready', revision:1,
  reason:'published', reason_text:'Ready.', size_bytes:2048, url:'/p/pv_0123456789abcdef/'};
const found = {previews:[preview], can_delete:true};
console.log(JSON.stringify({owner:cardPreviewsHTML(found),
  confirm:cardPreviewsHTML(found, preview.id),
  member:cardPreviewsHTML({...found,can_delete:false})}));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_rows_show_size_and_honest_containment_words():
    markup = rendered_rows()["owner"]
    assert "2 KiB" in markup, "show the size before an owner has to start deleting"
    assert "inside a viewer" in markup and "cannot move the viewer" in markup
    assert "no network requests" not in markup
    assert "Open only previews from people you trust" in markup


def test_delete_confirmation_names_the_question_for_assistive_technology():
    rows = rendered_rows()
    assert 'aria-label="Delete files' in rows["owner"]
    assert "data-preview-delete=" not in rows["member"]
    button = re.search(r"<button[^>]+data-preview-delete-confirm[^>]+>", rows["confirm"])[0]
    described = re.search(r'aria-describedby="([^"]+)"', button)
    assert described, "the confirmation question must be associated with Delete files"
    assert f'id="{described[1]}"' in rows["confirm"]
    assert "this cannot be undone" in rows["confirm"]
