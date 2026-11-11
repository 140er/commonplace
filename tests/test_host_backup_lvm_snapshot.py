"""Cleanup, trap and thaw-guard logic of deploy/backup/host-backup-lvm-snapshot.sh.

Every system tool (lvm, cryptsetup, clevis, fsfreeze, restic, ...) is a stub on PATH that
appends its command line to a call log, so nothing here touches a real volume.
"""

import json
import os
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "backup" / "host-backup-lvm-snapshot.sh"
GIB = 1024**3

STUBS = {
    "lvs": r"""
        echo "lvs $*" >> "$CALLS"
        name="${@: -1}"; name="${name#*/}"
        [ -e "$STATE/lv-$name" ] || exit 5
        case "$*" in *origin*) echo "  $(cat "$STATE/lv-$name")";; esac
    """,
    "lvcreate": r"""
        echo "lvcreate $*" >> "$CALLS"
        [ -z "${LVCREATE_FAIL:-}" ] || exit 5
        while [ $# -gt 0 ]; do [ "$1" = -n ] && name="$2"; shift; done
        echo srv > "$STATE/lv-$name"
        : > "$BACKUP_MAPPER_DIR/data-$name"
    """,
    "lvremove": r"""
        echo "lvremove $*" >> "$CALLS"
        n=$(cat "$STATE/lvremove-count" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$STATE/lvremove-count"
        [ "$n" -gt "${LVREMOVE_FAILS:-0}" ] || { echo "Logical volume is in use." >&2; exit 5; }
        name="${@: -1}"; rm -f "$STATE/lv-${name#*/}"
    """,
    "sync": r"""echo "sync" >> "$CALLS" """,
    "udevadm": r"""echo "udevadm $*" >> "$CALLS" """,
    "dmsetup": r"""
        echo "dmsetup $*" >> "$CALLS"
        [ -z "${DMSETUP_OK:-}" ] || rm -f "$BACKUP_MAPPER_DIR/${@: -1}"
    """,
    "vgs": r"""
        echo "vgs $*" >> "$CALLS"
        case "$*" in *"--units b"*) echo "  ${VG_FREE_BYTES:-0}";; *) echo vg;; esac
    """,
    "fsfreeze": r"""echo "fsfreeze $*" >> "$CALLS" """,
    "mountpoint": r"""exit 0""",
    "mount": r"""echo "mount $*" >> "$CALLS"; : > "$STATE/mounted" """,
    "umount": r"""echo "umount $*" >> "$CALLS"; rm -f "$STATE/mounted" """,
    "findmnt": r"""[ -e "$STATE/mounted" ]""",
    "clevis": r"""
        echo "clevis $* stdin=$(readlink /proc/self/fd/0)" >> "$CALLS"
        case "${CLEVIS_MODE:-ok}" in
            ok) : > "$BACKUP_MAPPER_DIR/${6}";;
            passphrase) echo "Enter passphrase for dev:"; exit 1;;
            *) echo "tang http://tang.invalid unreachable"; exit 1;;
        esac
    """,
    "cryptsetup": r"""
        echo "cryptsetup $*" >> "$CALLS"
        n=$(cat "$STATE/close-count" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$STATE/close-count"
        [ "$n" -gt "${CLOSE_FAILS:-0}" ] || { echo "Device $2 is still in use." >&2; exit 5; }
        rm -f "$BACKUP_MAPPER_DIR/$2"
    """,
    "unshare": r"""args="$*"; echo "unshare ${args//$'\n'/ }" >> "$CALLS"; : > "$STATE/backup-done" """,
    "restic": r"""
        echo "restic $*" >> "$CALLS"
        case "$1" in
            snapshots)
                if [ -e "$STATE/backup-done" ]; then echo '[{"id":"deadbeefcafe"}]'; else echo '[]'; fi;;
            check) exit "${STUB_CHECK_RC:-0}";;
        esac
    """,
    "hook": r"""
        echo "hook $*" >> "$CALLS"
        flock -n "$BACKUP_LOCK_DIR/.commonplace-backup.lock" true && echo "hook locks-free-1" >> "$CALLS"
        flock -n "$BACKUP_LOCK_DIR/.hook-backup.lock" true && echo "hook locks-free-2" >> "$CALLS"
    """,
}


@pytest.fixture
def host(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text("#!/bin/bash\n" + textwrap.dedent(body).strip() + "\n")
        stub.chmod(0o755)
    for sub in ("state", "mapper", "mnt", "srv/commonplace/backups"):
        (tmp_path / sub).mkdir(parents=True)
    (tmp_path / "restic-env").write_text("RESTIC_REPOSITORY=rest:https://repo.invalid/x\n")
    (tmp_path / "rest-password").write_text("not-a-real-password\n")
    calls = tmp_path / "calls.log"
    calls.touch()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "STATE": str(tmp_path / "state"),
        "BACKUP_SRV": str(tmp_path / "srv"),
        "BACKUP_LOCK_DIR": str(tmp_path / "srv/commonplace/backups"),
        "BACKUP_LOG_DIR": str(tmp_path / "log"),
        "BACKUP_MAPPER_DIR": str(tmp_path / "mapper"),
        "BACKUP_MNT_BASE": str(tmp_path / "mnt"),
        "BACKUP_RESTIC_ENV_FILE": str(tmp_path / "restic-env"),
        "BACKUP_RESTIC_REST_PASSWORD_FILE": str(tmp_path / "rest-password"),
        "BACKUP_HOOK": str(bin_dir / "hook"),
        "BACKUP_CLOSE_RETRY_SLEEP_S": "0",
        "BACKUP_LVREMOVE_RETRY_SLEEP_S": "0",
        "BACKUP_RESTIC_HOST": "testhost",
        "BACKUP_HOOK_LOCK_NAME": ".hook-backup.lock",
        "VG_FREE_BYTES": str(200 * GIB),
    }

    class Host:
        path = tmp_path

        def run(self, snippet=None, args=(), **extra):
            run_env = {**env, **extra}
            if snippet is None:
                cmd = ["bash", str(SCRIPT), *args]
            else:
                cmd = ["bash", "-c", f"source {SCRIPT}; set +e\n{snippet}"]
            return subprocess.run(cmd, env=run_env, capture_output=True, text=True, timeout=60)

        def calls(self):
            return calls.read_text().splitlines()

        def calls_for(self, tool):
            return [c for c in self.calls() if c.split()[0] == tool]

        def hook_runs(self):
            return [c for c in self.calls_for("hook") if "locks-free" not in c]

        def add_lv(self, name, origin="srv"):
            (tmp_path / "state" / f"lv-{name}").write_text(origin + "\n")

        def lv_exists(self, name):
            return (tmp_path / "state" / f"lv-{name}").exists()

        def open_mapper(self, name):
            (tmp_path / "mapper" / name).touch()

    return Host()


# ---- cleanup: only this run's temporary snapshot is ever removed ----


def test_cleanup_removes_only_its_own_snapshot(host):
    host.add_lv("srv")
    host.add_lv("nightlysnap20260101000000")
    host.open_mapper("srv")
    result = host.run(
        "init_run; SNAP_NAME=nightlysnap20260101000000; MAP=$SNAP_NAME; mkdir -p $LOG_DIR; : >> $LOG\n"
        "cleanup_snap; echo rc=$?"
    )
    assert "rc=0" in result.stdout, result.stderr
    assert host.calls_for("lvremove") == ["lvremove -y data/nightlysnap20260101000000"]
    assert host.lv_exists("srv") and not host.lv_exists("nightlysnap20260101000000")
    assert not any("srv" in c.split()[2:] for c in host.calls_for("cryptsetup"))


@pytest.mark.parametrize(
    ("name", "origin"),
    [("srv", "srv"), ("proof3snap1", "srv"), ("nightlysnap1", "other")],
    ids=["origin-lv", "unreserved-prefix", "wrong-origin"],
)
def test_cleanup_refuses_to_remove_foreign_volumes(host, name, origin):
    host.add_lv(name, origin)
    result = host.run(
        f"init_run; SNAP_NAME={name}; MAP=none; mkdir -p $LOG_DIR; : >> $LOG\ncleanup_snap; echo rc=$?"
    )
    assert "rc=1" in result.stdout, result.stderr
    assert host.calls_for("lvremove") == []
    assert host.lv_exists(name)


def test_cleanup_never_closes_the_live_srv_mapper(host):
    host.open_mapper("srv")
    result = host.run(
        "init_run; MAP=srv; UNLOCKED=1; SNAP_NAME=nightlysnap1; mkdir -p $LOG_DIR; : >> $LOG\n"
        "cleanup_snap; echo rc=$?"
    )
    assert "rc=1" in result.stdout, result.stderr
    assert host.calls_for("cryptsetup") == []


def test_cleanup_retries_busy_mapper_close_then_removes_snapshot(host):
    host.add_lv("nightlysnap1")
    host.open_mapper("nightlysnap1")
    (host.path / "state" / "mounted").touch()
    result = host.run(
        "init_run; SNAP_NAME=nightlysnap1; MAP=$SNAP_NAME; MNT=$MNT_BASE/x; MOUNTED=1; UNLOCKED=1\n"
        "mkdir -p $LOG_DIR; : >> $LOG\ncleanup_snap; echo rc=$?",
        CLOSE_FAILS="2",
    )
    assert "rc=0" in result.stdout, result.stderr
    closes = host.calls_for("cryptsetup")
    assert closes == ["cryptsetup close nightlysnap1"] * 3
    order = [
        c.split()[0] for c in host.calls() if c.split()[0] in {"umount", "cryptsetup", "lvremove"}
    ]
    assert order == ["umount", "cryptsetup", "cryptsetup", "cryptsetup", "lvremove"]


def test_cleanup_settles_devices_after_umount_before_closing(host):
    host.add_lv("nightlysnap1")
    host.open_mapper("nightlysnap1")
    (host.path / "state" / "mounted").touch()
    host.run(
        "init_run; SNAP_NAME=nightlysnap1; MAP=$SNAP_NAME; MNT=$MNT_BASE/x; MOUNTED=1; UNLOCKED=1\n"
        "mkdir -p $LOG_DIR; : >> $LOG\ncleanup_snap"
    )
    order = [
        c.split()[0] + (" settle" if c.startswith("udevadm settle") else "") for c in host.calls()
    ]
    order = [
        c for c in order if c in {"umount", "sync", "udevadm settle", "cryptsetup", "lvremove"}
    ]
    assert order[:4] == ["umount", "sync", "udevadm settle", "cryptsetup"]


def test_cleanup_tolerates_a_slow_mapper_release_with_the_default_retry_budget(host):
    host.add_lv("nightlysnap1")
    host.open_mapper("nightlysnap1")
    result = host.run(
        "init_run; SNAP_NAME=nightlysnap1; MAP=$SNAP_NAME; UNLOCKED=1\n"
        "mkdir -p $LOG_DIR; : >> $LOG\ncleanup_snap; echo rc=$?",
        CLOSE_FAILS="25",
    )
    assert "rc=0" in result.stdout, result.stderr
    assert len(host.calls_for("cryptsetup")) == 26
    assert host.calls_for("dmsetup") == []
    assert len(host.calls_for("lvremove")) == 1


def test_cleanup_falls_back_to_dmsetup_remove_for_its_own_mapper(host):
    host.add_lv("nightlysnap1")
    host.open_mapper("nightlysnap1")
    result = host.run(
        "init_run; SNAP_NAME=nightlysnap1; MAP=$SNAP_NAME; UNLOCKED=1; CLOSE_ATTEMPTS=3\n"
        "mkdir -p $LOG_DIR; : >> $LOG\ncleanup_snap; echo rc=$?",
        CLOSE_FAILS="99",
        DMSETUP_OK="1",
    )
    assert "rc=0" in result.stdout, result.stderr
    assert host.calls_for("dmsetup") == ["dmsetup remove --retry nightlysnap1"]
    assert len(host.calls_for("lvremove")) == 1


def test_cleanup_never_runs_dmsetup_against_the_live_srv_mapper(host):
    host.open_mapper("srv")
    host.run(
        "init_run; MAP=srv; UNLOCKED=1; SNAP_NAME=nightlysnap1; CLOSE_ATTEMPTS=1\n"
        "mkdir -p $LOG_DIR; : >> $LOG\ncleanup_snap",
        DMSETUP_OK="1",
    )
    assert host.calls_for("dmsetup") == [] and host.calls_for("cryptsetup") == []
    assert (host.path / "mapper" / "srv").exists()


def test_cleanup_retries_lvremove(host):
    host.add_lv("nightlysnap1")
    result = host.run(
        "init_run; SNAP_NAME=nightlysnap1; MAP=none\nmkdir -p $LOG_DIR; : >> $LOG\ncleanup_snap; echo rc=$?",
        LVREMOVE_FAILS="2",
    )
    assert "rc=0" in result.stdout, result.stderr
    assert len(host.calls_for("lvremove")) == 3 and not host.lv_exists("nightlysnap1")


def test_cleanup_fails_only_after_lvremove_retries_are_exhausted(host):
    host.add_lv("nightlysnap1")
    result = host.run(
        "init_run; SNAP_NAME=nightlysnap1; MAP=none\nmkdir -p $LOG_DIR; : >> $LOG\ncleanup_snap; echo rc=$?",
        LVREMOVE_FAILS="99",
    )
    assert "rc=1" in result.stdout, result.stderr
    assert len(host.calls_for("lvremove")) == 5


def test_cleanup_keeps_snapshot_when_mapper_never_closes(host):
    host.add_lv("nightlysnap1")
    host.open_mapper("nightlysnap1")
    result = host.run(
        "init_run; SNAP_NAME=nightlysnap1; MAP=$SNAP_NAME; UNLOCKED=1; CLOSE_ATTEMPTS=3\n"
        "mkdir -p $LOG_DIR; : >> $LOG\ncleanup_snap; echo rc=$?",
        CLOSE_FAILS="99",
    )
    assert "rc=1" in result.stdout, result.stderr
    assert len(host.calls_for("cryptsetup")) == 3
    assert host.calls_for("lvremove") == []


# ---- thaw guard and exit trap ----


def test_guard_thaws_srv_after_the_freeze_budget(host):
    result = host.run(
        "init_run; mkdir -p $LOG_DIR; : >> $LOG; FREEZE_BUDGET_S=1; start_guard; sleep 2.5; "
        "stop_guard; cat $LOG"
    )
    assert host.calls_for("fsfreeze") == [f"fsfreeze -u {host.path}/srv"], result.stderr
    assert "GUARD thawed" in result.stdout


def test_normal_thaw_stops_the_guard(host):
    host.run(
        "init_run; mkdir -p $LOG_DIR; : >> $LOG; FREEZE_BUDGET_S=1; start_guard; FROZEN=1; thaw; sleep 2"
    )
    assert host.calls_for("fsfreeze") == [f"fsfreeze -u {host.path}/srv"]


def test_exit_trap_thaws_removes_snapshot_and_records_failure(host):
    host.add_lv("nightlysnap1")
    result = host.run(
        "init_run; SNAP_NAME=nightlysnap1; MAP=$SNAP_NAME; mkdir -p $LOG_DIR; : >> $LOG\n"
        "trap on_exit EXIT; FROZEN=1; exit 3"
    )
    assert result.returncode == 3
    assert f"fsfreeze -u {host.path}/srv" in host.calls()
    assert host.calls_for("lvremove") == ["lvremove -y data/nightlysnap1"]
    assert (host.path / "log" / "result").read_text().startswith("FAIL unexpected exit status 3")


def test_logs_must_not_live_on_the_frozen_filesystem(host):
    result = host.run(args=(), BACKUP_LOG_DIR=str(host.path / "srv" / "logs"))
    assert result.returncode == 2
    assert "frozen" in result.stderr
    assert host.calls() == []


# ---- entry points ----


def test_help_does_not_start_a_backup(host):
    result = host.run(args=("--help",))
    assert result.returncode == 0 and "Usage" in result.stdout
    assert host.calls() == []
    assert not (host.path / "log").exists()


@pytest.mark.parametrize("flag", ["--check", "--latest-json"])
def test_monitoring_flags_go_to_the_hook(host, flag):
    result = host.run(args=(flag,))
    assert result.returncode == 0, result.stderr
    assert host.hook_runs() == [f"hook {flag}"]
    assert not any(c.startswith(("fsfreeze", "lvcreate")) for c in host.calls())


def test_backup_requires_host_configuration(host):
    result = host.run(BACKUP_RESTIC_HOST="")
    assert result.returncode == 2 and "BACKUP_RESTIC_HOST" in result.stderr
    assert host.calls() == []


def test_unknown_argument_is_rejected(host):
    assert host.run(args=("--nope",)).returncode == 2
    assert host.calls() == []


# ---- full flow with stubs ----


def test_snapshot_route_runs_restic_from_snapshot_and_cleans_up(host):
    result = host.run()
    assert result.returncode == 0, result.stderr
    calls = host.calls()
    assert host.hook_runs() == []
    assert [c for c in calls if c.startswith("fsfreeze")] == [
        f"fsfreeze -f {host.path}/srv",
        f"fsfreeze -u {host.path}/srv",
    ]
    (lvcreate,) = host.calls_for("lvcreate")
    assert "--snapshot -L 50G -n nightlysnap" in lvcreate and lvcreate.endswith("data/srv")
    assert "-Zn" not in lvcreate and "--zero" not in lvcreate and "noudevsync" not in lvcreate
    (clevis,) = host.calls_for("clevis")
    assert "-o --readonly" in clevis and clevis.endswith("stdin=/dev/null")
    (mount,) = host.calls_for("mount")
    assert "-o ro,noload,norecovery" in mount and "nouuid" not in mount
    (unshare,) = host.calls_for("unshare")
    assert "--mount" in unshare and "mount --bind" in unshare and "remount,ro" in unshare
    for needle in (
        "testhost",
        "nightly",
        "--one-file-system",
        "/srv/docker",
        "/srv/voice",
        "/etc",
        "/var/lib/caddy",
    ):
        assert needle in unshare
    restic = [c.split()[1] for c in host.calls_for("restic")]
    assert restic == ["snapshots", "snapshots", "check"]
    assert host.calls_for("lvremove")[0].startswith("lvremove -y data/nightlysnap")
    assert not any(host.lv_exists(p.name[3:]) for p in host.path.glob("state/lv-*"))
    assert (host.path / "log" / "result").read_text().startswith("PASS")
    proof = json.loads((host.path / "log" / "lvm-proof.json").read_text())
    assert proof["restic_snapshot_id"] == "deadbeefcafe" and proof["cleanup_ok"] == 1


def test_retried_cleanup_after_good_backup_is_still_pass(host):
    result = host.run(CLOSE_FAILS="5", LVREMOVE_FAILS="1")
    assert result.returncode == 0, result.stderr
    assert (host.path / "log" / "result").read_text().startswith("PASS")
    assert len(host.calls_for("cryptsetup")) == 6 and len(host.calls_for("lvremove")) == 2
    proof = json.loads((host.path / "log" / "lvm-proof.json").read_text())
    assert proof["cleanup_ok"] == 1 and proof["fail_reason"] == ""
    assert host.hook_runs() == []


def test_restic_check_failure_fails_without_fallback_and_still_cleans_up(host):
    result = host.run(STUB_CHECK_RC="1")
    assert result.returncode == 1
    assert host.hook_runs() == []
    assert len(host.calls_for("lvremove")) == 1
    assert (host.path / "log" / "result").read_text().startswith("FAIL restic check exited 1")


@pytest.mark.parametrize("mode", ["passphrase", "unreachable"])
def test_failed_unlock_stops_cleans_up_and_falls_back_with_locks_released(host, mode):
    result = host.run(CLEVIS_MODE=mode)
    assert result.returncode == 0, result.stderr
    assert len(host.calls_for("clevis")) == 1
    assert host.calls_for("restic") == [] and host.calls_for("mount") == []
    assert len(host.calls_for("lvremove")) == 1
    calls = host.calls()
    hook_lines = [c for c in calls if c.startswith("hook")]
    assert hook_lines == ["hook ", "hook locks-free-1", "hook locks-free-2"]
    assert max(i for i, c in enumerate(calls) if c.startswith("lvremove")) < calls.index("hook ")
    assert (host.path / "log" / "result").read_text().startswith("FALLBACK clevis luks unlock")
    assert not any((host.path / "mapper").glob("nightlysnap*"))


def test_small_vg_falls_back_before_any_freeze(host):
    result = host.run(VG_FREE_BYTES=str(53 * GIB))
    assert result.returncode == 0, result.stderr
    assert host.calls_for("fsfreeze") == [] and host.calls_for("lvcreate") == []
    assert host.hook_runs() == ["hook "]


def test_lvcreate_failure_thaws_and_falls_back(host):
    result = host.run(LVCREATE_FAIL="1")
    assert result.returncode == 0, result.stderr
    assert host.calls_for("fsfreeze")[-1] == f"fsfreeze -u {host.path}/srv"
    assert host.calls_for("clevis") == []
    assert host.hook_runs() == ["hook "]


def test_held_lock_fails_without_running_the_hook(host):
    lock = host.path / "srv/commonplace/backups/.hook-backup.lock"
    holder = subprocess.Popen(["flock", str(lock), "sleep", "10"])
    try:
        time.sleep(0.3)
        result = host.run()
    finally:
        holder.kill()
        holder.wait()
    assert result.returncode == 1
    assert host.hook_runs() == [] and host.calls_for("fsfreeze") == []
