"""The commonplace CLI: setup, tokens shown once, revoke, audit."""

from commonplace.cli import main
from commonplace.control_db import ControlDB


def test_setup_token_list_revoke_audit(tmp_path, capsys):
    data = ["--data", str(tmp_path)]
    assert main(data + ["init", "--admin-email", "me@example.com"]) == 0
    assert main(data + ["init", "--admin-email", "me@example.com"]) == 2
    capsys.readouterr()
    assert main(data + ["user", "add", "friend@example.com"]) == 0
    assert main(data + ["member", "add", "personal", "friend@example.com"]) == 0
    assert main(data + ["user", "list"]) == 0
    assert main(data + ["workspace", "list"]) == 0
    capsys.readouterr()
    assert (
        main(
            data
            + [
                "token",
                "add",
                "grok:cos",
                "--workspace",
                "personal",
                "--client",
                "grok",
                "--read-wings",
                "shared,inbox",
                "--write-wing",
                "inbox",
            ]
        )
        == 0
    )
    out = capsys.readouterr()
    token = out.out.strip()
    assert token.startswith("cmmn_") and "\n" not in token
    assert '"trust": "untrusted"' in out.err
    assert main(data + ["token", "list"]) == 0
    listing = capsys.readouterr().out
    assert "grok:cos" in listing and token not in listing and token[-4:] in listing
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    assert db.authenticate_token(token).connection == "grok:cos"
    assert main(data + ["token", "revoke", "grok:cos"]) == 0
    assert db.authenticate_token(token) is None
    assert main(data + ["audit"]) == 0
    assert "token.revoke" in capsys.readouterr().out
    # A member can't make tokens; bad policies are refused with exit 2.
    assert (
        main(data + ["token", "add", "x", "--workspace", "personal", "--by", "friend@example.com"])
        == 2
    )
    assert (
        main(data + ["token", "add", "y", "--workspace", "personal", "--scopes", "memory:all"]) == 2
    )
    assert "commonplace:" in capsys.readouterr().err


def test_serve_needs_setup(tmp_path, capsys):
    assert main(["--data", str(tmp_path), "serve"]) == 2
