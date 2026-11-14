"""Runner installer additions preserve the existing command defaults."""

from commonplace import cli


def test_install_commands_are_available_without_changing_existing_defaults():
    parser = cli.build_parser()
    commands = parser._subparsers._group_actions[0].choices
    runner = commands["runner"]._subparsers._group_actions[0].choices
    assert {"install", "status", "uninstall"} <= runner.keys()
    for action in ("check", "run", "chat"):
        args = parser.parse_args(["runner", action, "--config", "fixture.json"])
        assert args.config == "fixture.json"
        assert args.func is cli.cmd_runner
        if action != "check":
            assert args.once is False
