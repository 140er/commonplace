#!/bin/sh
# Explicit disposable fixture only; no persistent unit or machine configuration.
set -eu
: "${COMMONPLACE_COMPUTE_SANDBOX_FIXTURE:?set the disposable fixture config path}"
: "${FIXTURE_PYTHON:?set the offline fixture Python interpreter}"
export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/systemd-run --user --scope --quiet --collect -p Delegate=yes -- \
    "$FIXTURE_PYTHON" -B - <<'PY'
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if os.geteuid() == 0:
    raise SystemExit("fixture must run as its non-root user")
config_path = Path(os.environ["COMMONPLACE_COMPUTE_SANDBOX_FIXTURE"])
settings = json.loads(config_path.read_text())
if settings["workspace"] != "fixture-script-workspace":
    raise SystemExit("reserved disposable workspace identity required")
relative = next(
    line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines()
    if line.startswith("0::")
)
base = Path("/sys/fs/cgroup") / relative.lstrip("/")
if not base.name.endswith(".scope"):
    raise SystemExit("transient systemd scope required")
properties = subprocess.check_output(
    ["systemctl", "--user", "show", base.name, "-p", "Delegate", "-p", "ControlGroup"],
    text=True,
)
print(properties, end="", flush=True)
if "Delegate=yes" not in properties.splitlines():
    raise SystemExit("delegated scope required")
# Match the runner's systemd layout: processes in runner, job groups in fixture.
runner, fixture = base / "runner", base / "fixture"
runner.mkdir(mode=0o700)
(runner / "cgroup.procs").write_text(str(os.getpid()))
(base / "cgroup.subtree_control").write_text("+cpu +memory +pids")
fixture.mkdir(mode=0o700)
(fixture / "cgroup.subtree_control").write_text("+cpu +memory +pids")
for label, directory in (("scope", base), ("runner", runner), ("fixture", fixture)):
    members = (directory / "cgroup.procs").read_text().split()
    print(f"{label} cgroup.procs: {members}", flush=True)
assert not (base / "cgroup.procs").read_text().strip()
assert str(os.getpid()) in (runner / "cgroup.procs").read_text().split()
assert not (fixture / "cgroup.procs").read_text().strip()
settings["cgroup_root"] = str(fixture)
with tempfile.TemporaryDirectory(prefix="scope-run-", dir=config_path.parent) as temporary:
    temporary = Path(temporary)
    scoped_config = temporary / "config.json"
    fd = os.open(scoped_config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(settings, stream)
    env = dict(os.environ, COMMONPLACE_COMPUTE_SANDBOX_FIXTURE=str(scoped_config),
               TMPDIR=str(temporary))
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-v", "--tb=short", "-p", "no:cacheprovider",
         "--basetemp=" + str(temporary / "pytest"),
         "tests/test_compute_script_sandbox.py::test_optional_actual_disposable_confined_script",
         "tests/test_compute_ssh_fixture.py::test_encrypted_custody_to_actual_two_hop_ssh_without_network"],
        env=env,
    )
    for label, directory in (("scope", base), ("runner", runner), ("fixture", fixture)):
        print(f"{label} cgroup.procs after tests: "
              f"{(directory / 'cgroup.procs').read_text().split()}", flush=True)
    raise SystemExit(result.returncode)
PY
