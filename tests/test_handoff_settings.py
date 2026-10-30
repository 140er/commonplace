"""Settings contract tests, with no worker, database or network dependencies."""

from dataclasses import asdict, replace
from itertools import product

import pytest

from commonplace.handoff.models import (
    DEFAULT_THRESHOLD,
    EVENT_SETTINGS,
    HandoffSettings,
    LauncherConfig,
    SettingRow,
)
from commonplace.handoff.settings import (
    THRESHOLD_MAX,
    THRESHOLD_MIN,
    SettingsError,
    SettingsService,
    parse_launcher,
    resolve,
    validate,
)

OWNER_ERROR = "only workspace owners can change this"
COMMAND = LauncherConfig(kind="command", argv=("runner", "{prompt_file}"))
WEBHOOK = LauncherConfig(kind="webhook", url="http://localhost:9000/start", secret="s" * 16)
SCOPES = (("workspace", ""), ("agent", "agent-one"), ("task", "#42"))


class FakeDB:
    def __init__(self, rows=(), *, fail_on=""):
        self.rows = {(row.workspace_id, row.scope, row.ref): row for row in rows}
        self.calls = []
        self.fail_on = fail_on

    def setting_rows(self, workspace_id, agent="", task_ref=""):
        self.calls.append(("read", workspace_id, agent, task_ref))
        if self.fail_on == "read":
            raise RuntimeError("read failed")
        return [
            self.rows[key]
            for key in (
                (workspace_id, "workspace", ""),
                (workspace_id, "agent", agent),
                (workspace_id, "task", task_ref),
            )
            if key in self.rows
        ]

    def put_setting(self, row, *, actor):
        self.calls.append(("put", row, actor))
        if self.fail_on == "put":
            raise RuntimeError("put failed")
        saved = replace(row, updated_by=actor, updated_at="2026-10-30T00:00:00Z")
        self.rows[(saved.workspace_id, saved.scope, saved.ref)] = saved
        return saved

    def delete_setting(self, workspace_id, scope, ref, *, actor):
        self.calls.append(("delete", workspace_id, scope, ref, actor))
        if self.fail_on == "delete":
            raise RuntimeError("delete failed")
        return self.rows.pop((workspace_id, scope, ref), None) is not None


class FakeEvents:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def emit(self, event, **fields):
        self.calls.append((event, fields))
        if self.fail:
            raise RuntimeError("event failed")


def test_default_settings_are_off_and_use_the_model_window():
    assert resolve([]) == HandoffSettings(
        False, DEFAULT_THRESHOLD, None, LauncherConfig(), "default"
    )


@pytest.mark.parametrize("values", product(("missing", None, False, True), repeat=3))
def test_enabled_resolution_in_every_scope_combination(values):
    rows = []
    expected, source = False, "default"
    for (scope, ref), value in zip(SCOPES, values):
        if value == "missing":
            continue
        rows.append(SettingRow("ws", scope, ref, enabled=value))
        if value is not None:
            expected, source = value, scope
    before = list(rows)
    actual = resolve(rows)
    assert actual == HandoffSettings(enabled=expected, source=source)
    assert rows == before


@pytest.mark.parametrize(
    ("field", "overrides", "default"),
    [
        ("threshold", (0.6, 0.7, 0.9), DEFAULT_THRESHOLD),
        ("context_window", (8_000, 200_000, 10_000_000), None),
        ("launcher", (COMMAND, WEBHOOK, LauncherConfig()), LauncherConfig()),
    ],
)
@pytest.mark.parametrize("present", product((False, True), repeat=3))
def test_each_field_inherits_independently(field, overrides, default, present):
    rows = []
    expected = default
    for (scope, ref), value, supplied in zip(SCOPES, overrides, present):
        rows.append(SettingRow("ws", scope, ref, **{field: value if supplied else None}))
        if supplied:
            expected = value
    actual = resolve(rows)
    assert getattr(actual, field) == expected
    assert actual.enabled is False
    assert actual.source == "default"


def test_resolution_keeps_other_fields_and_tracks_only_enabled_source():
    rows = [
        SettingRow("ws", "workspace", "", enabled=True, threshold=0.7, launcher=COMMAND),
        SettingRow("ws", "agent", "agent-one", enabled=False, context_window=400_000),
        SettingRow("ws", "task", "#42", threshold=0.9),
    ]
    assert resolve(rows) == HandoffSettings(False, 0.9, 400_000, COMMAND, "agent")
    assert resolve(rows + [replace(rows[2], enabled=True, launcher=LauncherConfig())]) == (
        HandoffSettings(True, 0.9, 400_000, LauncherConfig(), "task")
    )


def test_resolution_applies_rows_in_supplied_order():
    rows = [
        SettingRow("ws", scope, ref, enabled=True, threshold=value)
        for (scope, ref), value in zip(SCOPES, (0.6, 0.7, 0.9))
    ]
    assert resolve(rows[::-1]) == HandoffSettings(True, 0.6, source="workspace")


@pytest.mark.parametrize("enabled", [None, False, True])
def test_valid_enabled(enabled):
    row = SettingRow("ws", "workspace", "", enabled=enabled)
    assert validate(row, is_owner=True) == row


@pytest.mark.parametrize("enabled", [0, 1, "true", "false", "", [], {}])
def test_invalid_enabled(enabled):
    with pytest.raises(SettingsError, match="enabled"):
        validate(SettingRow("ws", "task", "#1", enabled=enabled), is_owner=True)


@pytest.mark.parametrize("threshold", [None, THRESHOLD_MIN, 0.8, THRESHOLD_MAX])
def test_valid_threshold(threshold):
    row = SettingRow("ws", "workspace", "", threshold=threshold)
    assert validate(row, is_owner=True) == row


@pytest.mark.parametrize(
    "threshold",
    [0, 1, 0.49, 0.96, float("nan"), float("inf"), -float("inf"), "0.8", True, False, [], {}],
)
def test_invalid_threshold(threshold):
    with pytest.raises(SettingsError, match="threshold"):
        validate(SettingRow("ws", "workspace", "", threshold=threshold), is_owner=True)


@pytest.mark.parametrize("window", [None, 8_000, 200_000, 10_000_000])
def test_valid_context_window(window):
    row = SettingRow("ws", "workspace", "", context_window=window)
    assert validate(row, is_owner=True) == row


@pytest.mark.parametrize(
    "window",
    [0, -1, 7_999, 10_000_001, 8_000.0, float("nan"), float("inf"), "8000", True, False, [], {}],
)
def test_invalid_context_window(window):
    with pytest.raises(SettingsError, match="context window"):
        validate(SettingRow("ws", "workspace", "", context_window=window), is_owner=True)


VALID_REFS = [
    ("workspace", "", ""),
    ("agent", "a", "a"),
    ("agent", "0", "0"),
    ("agent", "A-z.1_2:3@4-5", "A-z.1_2:3@4-5"),
    ("agent", "a" * 120, "a" * 120),
    ("task", "142", "#142"),
    ("task", "#142", "#142"),
    ("task", "00142", "#142"),
    ("task", "#00142", "#142"),
    ("task", "0", "#0"),
    ("task", "#0", "#0"),
    ("task", "١٤٢", "#142"),
    ("task", "task", "task"),
    ("task", "#task.name-1", "#task.name-1"),
    ("task", "_task", "_task"),
    ("task", "é", "é"),
    ("task", "a" * 64, "a" * 64),
    ("task", "#" + "a" * 64, "#" + "a" * 64),
]

INVALID_REFS = [
    ("", ""),
    ("member", "a"),
    (None, ""),
    ([], ""),
    ("workspace", "a"),
    ("workspace", " "),
    ("workspace", None),
    ("workspace", 0),
    ("agent", ""),
    ("agent", "-a"),
    ("agent", ".a"),
    ("agent", "_a"),
    ("agent", "a/b"),
    ("agent", "a b"),
    ("agent", "a\nb"),
    ("agent", "a;b"),
    ("agent", "a{b}"),
    ("agent", "é"),
    ("agent", "a" * 121),
    ("agent", None),
    ("task", ""),
    ("task", "#"),
    ("task", "##1"),
    ("task", "-a"),
    ("task", ".a"),
    ("task", "a/b"),
    ("task", "a b"),
    ("task", "a:b"),
    ("task", "a@b"),
    ("task", "a\n"),
    ("task", "a" * 65),
    ("task", "#" + "a" * 65),
    ("task", 42),
]


@pytest.mark.parametrize(("scope", "ref", "expected"), VALID_REFS)
def test_valid_refs_and_numeric_normalization(scope, ref, expected):
    row = SettingRow("ws", scope, ref, updated_by="old", updated_at="then")
    actual = validate(row, is_owner=True)
    assert actual == replace(row, ref=expected)
    assert row.ref == ref


@pytest.mark.parametrize(("scope", "ref"), INVALID_REFS)
def test_invalid_refs(scope, ref):
    with pytest.raises(SettingsError):
        validate(SettingRow("ws", scope, ref), is_owner=True)


@pytest.mark.parametrize("enabled", [False, True])
def test_members_may_only_set_task_enabled(enabled):
    row = SettingRow("ws", "task", "0042", enabled=enabled)
    assert validate(row, is_owner=False) == replace(row, ref="#42")


MEMBER_DENIED = [
    SettingRow("ws", "workspace", ""),
    SettingRow("ws", "workspace", "", enabled=True),
    SettingRow("ws", "workspace", "", enabled=False),
    SettingRow("ws", "agent", "agent-one", enabled=True),
    SettingRow("ws", "task", "#42"),
    SettingRow("ws", "task", "#42", threshold=0.8),
    SettingRow("ws", "task", "#42", enabled=True, threshold=0.8),
    SettingRow("ws", "task", "#42", enabled=False, context_window=8_000),
    SettingRow("ws", "task", "#42", enabled=True, launcher=LauncherConfig()),
    SettingRow("ws", "task", "#42", enabled=True, launcher=COMMAND),
    SettingRow("ws", "task", "#42", enabled=True, launcher=WEBHOOK),
    SettingRow("ws", "other", "", threshold="invalid"),
]


@pytest.mark.parametrize("row", MEMBER_DENIED)
def test_members_need_owner_permission_for_every_other_change(row):
    with pytest.raises(SettingsError, match=f"^{OWNER_ERROR}$"):
        validate(row, is_owner=False)


@pytest.mark.parametrize(("scope", "ref"), SCOPES)
def test_owner_may_set_every_field_at_every_scope(scope, ref):
    row = SettingRow(
        "ws", scope, ref, enabled=True, threshold=0.9, context_window=8_000, launcher=WEBHOOK
    )
    assert validate(row, is_owner=True) == row


@pytest.mark.parametrize("enabled", [0, "true"])
def test_members_still_need_a_boolean_enabled(enabled):
    with pytest.raises(SettingsError, match="enabled"):
        validate(SettingRow("ws", "task", "#1", enabled=enabled), is_owner=False)


@pytest.mark.parametrize("data", [None, {}, {"kind": "client"}])
def test_parse_launcher_defaults(data):
    assert parse_launcher(data) == (None if data is None else LauncherConfig())


@pytest.mark.parametrize(
    "executable",
    [
        "runner",
        "runner.exe",
        "runner-1.2",
        "_runner",
        ".runner",
        "/usr/bin/runner",
        "/opt/My Tool/runner",
        r"C:\Program Files\Tool\runner.exe",
        "C:/Tools/runner.exe",
        r"\\server\share\runner.exe",
    ],
)
def test_command_executable_names_and_absolute_paths(executable):
    assert parse_launcher({"kind": "command", "argv": [executable, ""]}) == (
        LauncherConfig(kind="command", argv=(executable, ""))
    )


def test_command_placeholders_and_escaped_braces_are_preserved():
    argv = [
        "runner",
        "{prompt}",
        "{prompt_file}",
        "{handoff_id}",
        "{thread_id}",
        "{cwd}",
        'prefix {{"id":"{handoff_id}"}} suffix',
    ]
    config = parse_launcher({"kind": "command", "argv": argv})
    assert config.argv == tuple(argv)
    argv.append("later")
    assert len(config.argv) == 7


INVALID_LAUNCHERS = [
    {"kind": "unknown"},
    {"kind": ""},
    {"kind": None},
    {"kind": []},
    {"argv": "runner"},
    {"argv": None},
    {"argv": 1},
    {"argv": {}},
    {"argv": ["runner", 1]},
    {"argv": ["runner", None]},
    {"argv": ["runner", True]},
    {"argv": ["runner", []]},
    {"argv": ["runner", {}]},
    {"argv": ["runner", "a\0b"]},
    {"url": None},
    {"url": 42},
    {"secret": None},
    {"secret": 42},
    {"kind": "command"},
    {"kind": "command", "argv": []},
    *(
        {"kind": "command", "argv": [value]}
        for value in (
            "",
            " ",
            ".",
            "..",
            "runner --flag",
            "./runner",
            "../runner",
            "folder/runner",
            r".\runner",
            r"C:runner.exe",
            r"\runner.exe",
            "{cwd}",
            "$HOME/bin/runner",
            "~/runner",
            "runner;echo",
            "runner|echo",
            "runner&&echo",
            "/bin/run\nner",
        )
    ),
    *(
        {"kind": "command", "argv": ["runner", value]}
        for value in (
            "{unknown}",
            "{prompt.name}",
            "{prompt[0]}",
            "{}",
            "{0}",
            "{prompt!r}",
            "{prompt!s}",
            "{prompt:>10}",
            "{prompt:{cwd}}",
            "{",
            "}",
            "{prompt",
        )
    ),
    *(
        {"kind": "webhook", "url": value, "secret": "s" * 16}
        for value in (
            "",
            "ftp://localhost/start",
            "localhost/start",
            "//localhost/start",
            "http:///start",
            "https://",
            "http://?start",
            "http://:80/start",
            "http://[::1",
            "http://[bad]/",
            "http://localhost:bad",
            "http://localhost:65536",
            "http://local host/start",
            "http://localhost/start here",
            "http://localhost/\nstart",
            "http://localhost/\0start",
        )
    ),
    {"kind": "webhook", "url": "http://localhost", "secret": ""},
    {"kind": "webhook", "url": "http://localhost", "secret": "s" * 15},
]


@pytest.mark.parametrize("data", INVALID_LAUNCHERS)
@pytest.mark.parametrize("entry", ["parse", "row"])
def test_launcher_validation_at_both_entry_points(data, entry):
    with pytest.raises(SettingsError):
        if entry == "parse":
            parse_launcher(data)
        else:
            row = SettingRow("ws", "workspace", "", launcher=LauncherConfig(**data))
            validate(row, is_owner=True)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:9000/start?mode=new",
        "https://example.test/start",
        "http://127.0.0.1/start",
        "http://[::1]:9000/start",
        "HTTPS://example.test/start",
    ],
)
@pytest.mark.parametrize("secret", ["s" * 16, "s" * 17, "é" * 16])
def test_valid_webhook_launcher(url, secret):
    data = {"kind": "webhook", "url": url, "secret": secret}
    assert parse_launcher(data) == LauncherConfig(**data)


@pytest.mark.parametrize("data", [[], "client", False, 1, ()])
def test_parse_launcher_requires_an_object(data):
    with pytest.raises(SettingsError, match="launcher must be an object"):
        parse_launcher(data)


@pytest.mark.parametrize(
    "data",
    [{"shell": True}, {"kind": "client", "extra": False}, {"argv": [], "env": {}}, {1: "value"}],
)
def test_parse_launcher_rejects_unknown_keys(data):
    with pytest.raises(SettingsError, match="unknown launcher fields"):
        parse_launcher(data)


@pytest.mark.parametrize("launcher", [{}, "client", False])
def test_validate_requires_a_launcher_config(launcher):
    with pytest.raises(SettingsError, match="launcher configuration"):
        validate(SettingRow("ws", "workspace", "", launcher=launcher), is_owner=True)


def test_validate_freezes_launcher_argv_without_mutating_the_input():
    argv = ["runner", "{prompt}"]
    row = SettingRow("ws", "workspace", "", launcher=LauncherConfig(kind="command", argv=argv))
    actual = validate(row, is_owner=True)
    argv.append("later")
    assert actual.launcher == LauncherConfig(kind="command", argv=("runner", "{prompt}"))
    assert row.launcher.argv is argv


def test_effective_uses_db_scope_order_and_workspace_isolation():
    rows = [
        SettingRow("ws", "workspace", "", enabled=True, threshold=0.7),
        SettingRow("ws", "agent", "agent-one", enabled=False),
        SettingRow("ws", "task", "#42", enabled=True, context_window=8_000),
        SettingRow("other", "task", "#42", enabled=False),
    ]
    db = FakeDB(rows)
    service = SettingsService(db)
    assert service.effective("ws", "agent-one", "#42") == HandoffSettings(
        True, 0.7, 8_000, source="task"
    )
    assert db.calls == [("read", "ws", "agent-one", "#42")]
    assert service.effective("other", "agent-one", "#42").enabled is False
    assert service.effective("missing") == HandoffSettings()
    assert db.calls[-1] == ("read", "missing", "", "")


def test_set_validates_normalizes_and_returns_the_database_stamp():
    db, events = FakeDB(), FakeEvents()
    service = SettingsService(db, events)
    row = SettingRow("ws", "task", "0042", enabled=True, launcher=WEBHOOK, updated_by="untrusted")
    saved = service.set(row, actor="owner", is_owner=True)
    assert saved == replace(row, ref="#42", updated_by="owner", updated_at="2026-10-30T00:00:00Z")
    assert db.calls == [("put", replace(row, ref="#42"), "owner")]
    assert db.rows[("ws", "task", "#42")] is saved
    assert events.calls == [(EVENT_SETTINGS, {"workspace_id": "ws", "reason": "set task #42"})]
    assert "secret" not in str(events.calls)
    assert WEBHOOK.secret not in str(events.calls)
    assert row.ref == "0042"


def test_members_can_set_task_enabled_through_the_service():
    db = FakeDB()
    saved = SettingsService(db).set(
        SettingRow("ws", "task", "42", enabled=False), actor="member", is_owner=False
    )
    assert saved.ref == "#42"
    assert saved.updated_by == "member"
    assert saved.enabled is False


@pytest.mark.parametrize("row", MEMBER_DENIED)
def test_service_rejects_member_changes_before_writing_or_emitting(row):
    db, events = FakeDB(), FakeEvents()
    with pytest.raises(SettingsError, match=f"^{OWNER_ERROR}$"):
        SettingsService(db, events).set(row, actor="member", is_owner=False)
    assert db.calls == events.calls == []


def test_service_rejects_invalid_owner_changes_before_writing():
    db, events = FakeDB(), FakeEvents()
    with pytest.raises(SettingsError, match="threshold"):
        SettingsService(db, events).set(
            SettingRow("ws", "workspace", "", threshold=0.2), actor="owner", is_owner=True
        )
    assert db.calls == events.calls == []


@pytest.mark.parametrize(("scope", "ref"), SCOPES)
def test_owner_clear_returns_the_database_result_and_emits_only_a_change(scope, ref):
    row = SettingRow("ws", scope, ref, enabled=True, threshold=0.7)
    db, events = FakeDB([row]), FakeEvents()
    service = SettingsService(db, events)
    supplied_ref = "0042" if scope == "task" else ref
    assert service.clear("ws", scope, supplied_ref, actor="owner", is_owner=True) is True
    assert db.calls == [("delete", "ws", scope, ref, "owner")]
    assert events.calls == [
        (EVENT_SETTINGS, {"workspace_id": "ws", "reason": f"clear {scope} {ref}".rstrip()})
    ]
    assert service.clear("ws", scope, ref, actor="owner", is_owner=True) is False
    assert len(events.calls) == 1


def test_clearing_restores_inherited_values_without_affecting_other_workspaces():
    db = FakeDB(
        [
            SettingRow("ws", "workspace", "", enabled=True, threshold=0.7),
            SettingRow("ws", "task", "#42", enabled=False, threshold=0.9),
            SettingRow("other", "task", "#42", enabled=False),
        ]
    )
    service = SettingsService(db)
    assert service.effective("ws", task_ref="#42").enabled is False
    assert service.clear("ws", "task", "42", actor="owner", is_owner=True) is True
    assert service.effective("ws", task_ref="#42") == HandoffSettings(True, 0.7, source="workspace")
    assert ("other", "task", "#42") in db.rows


@pytest.mark.parametrize(("scope", "ref"), SCOPES)
def test_clear_is_owner_only_even_for_task_enabled(scope, ref):
    db, events = FakeDB([SettingRow("ws", scope, ref, enabled=True)]), FakeEvents()
    with pytest.raises(SettingsError, match=f"^{OWNER_ERROR}$"):
        SettingsService(db, events).clear("ws", scope, ref, actor="member", is_owner=False)
    assert db.calls == events.calls == []


@pytest.mark.parametrize(("scope", "ref"), INVALID_REFS)
def test_clear_validates_scope_and_ref_before_deleting(scope, ref):
    db, events = FakeDB(), FakeEvents()
    with pytest.raises(SettingsError):
        SettingsService(db, events).clear("ws", scope, ref, actor="owner", is_owner=True)
    assert db.calls == events.calls == []


@pytest.mark.parametrize("operation", ["read", "put", "delete"])
def test_database_errors_propagate_without_emitting(operation):
    db, events = FakeDB(fail_on=operation), FakeEvents()
    service = SettingsService(db, events)
    with pytest.raises(RuntimeError, match=f"{operation} failed"):
        if operation == "read":
            service.effective("ws")
        elif operation == "put":
            service.set(
                SettingRow("ws", "workspace", "", enabled=True), actor="owner", is_owner=True
            )
        else:
            service.clear("ws", "workspace", "", actor="owner", is_owner=True)
    assert events.calls == []


@pytest.mark.parametrize("operation", ["set", "clear"])
def test_event_errors_propagate_after_the_database_change(operation):
    row = SettingRow("ws", "workspace", "", enabled=True)
    db, events = FakeDB([row] if operation == "clear" else []), FakeEvents(fail=True)
    service = SettingsService(db, events)
    with pytest.raises(RuntimeError, match="event failed"):
        if operation == "set":
            service.set(row, actor="owner", is_owner=True)
        else:
            service.clear("ws", "workspace", "", actor="owner", is_owner=True)
    assert bool(db.rows) is (operation == "set")
    assert len(events.calls) == 1


def test_set_and_clear_with_no_events():
    service = SettingsService(FakeDB())
    saved = service.set(
        SettingRow("ws", "workspace", "", enabled=True), actor="owner", is_owner=True
    )
    assert service.effective("ws").enabled is True
    assert (
        service.clear(saved.workspace_id, saved.scope, saved.ref, actor="owner", is_owner=True)
        is True
    )
    assert service.effective("ws") == HandoffSettings()


def test_falsey_events_object_is_still_used():
    class FalseyEvents(FakeEvents):
        def __bool__(self):
            return False

    events = FalseyEvents()
    service = SettingsService(FakeDB(), events)
    service.set(SettingRow("ws", "workspace", "", enabled=True), actor="owner", is_owner=True)
    assert events.calls == [(EVENT_SETTINGS, {"workspace_id": "ws", "reason": "set workspace"})]


def test_public_launcher_data_omits_the_secret_after_validation():
    launcher = parse_launcher(asdict(WEBHOOK))
    assert launcher.public() == {"kind": "webhook", "argv": [], "url": WEBHOOK.url}
