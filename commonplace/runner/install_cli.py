"""Argument definitions stay light so existing commands keep their startup path."""

from pathlib import Path


def add_parsers(runner, handler):
    for action in ("install", "status", "uninstall", "_setup-service"):
        parser = runner.add_parser(action)
        parser.add_argument("--home", default=str(Path.home()))
        parser.set_defaults(func=handler)
        if action != "install":
            continue
        for flag in ("hub", "workspace", "label", "manifest-digest", "enrollment-id"):
            parser.add_argument("--" + flag, required=True)
        parser.add_argument("--plan", action="store_true")
        parser.add_argument("--accept", action="store_true")
        parser.add_argument("--release")
        parser.add_argument("--release-signature")
        parser.add_argument("--release-key")
