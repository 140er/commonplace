"""Deployment invariants and the host backup hook, without Docker or a network."""

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
DOCKERFILE = DEPLOY / "commonplace" / "Dockerfile"
COMPOSE = DEPLOY / "compose.edge1.yml"
CADDY = DEPLOY / "commonplace.caddy"
HOOK = ROOT / "backup-hook.sh"


def test_image_installs_repo_and_runs_as_nonroot():
    text = DOCKERFILE.read_text()
    assert text.startswith("FROM python:3.12-slim\n")
    assert "RUN pip install .\n" in text
    users = re.findall(r"^USER (.+)$", text, re.MULTILINE)
    assert users == ["10001:10001"]
    assert "COMMONPLACE_DATA=/data" in text
    assert 'VOLUME ["/data"]' in text
    command = json.loads(re.search(r"^CMD (.+)$", text, re.MULTILINE)[1])
    assert command == ["commonplace", "serve", "--host", "0.0.0.0", "--port", "8765"]


def test_model_is_preloaded_as_runtime_user_outside_data_volume():
    text = DOCKERFILE.read_text()
    assert "HOME=/home/commonplace" in text
    runtime_build = text.split("USER 10001:10001\n", 1)[1]
    assert "from mempalace.embedding import get_embedding_function" in runtime_build
    assert "device='cpu', model='minilm'" in runtime_build
    assert "input=['Commonplace build check']" in runtime_build
    assert "assert len(vectors[0]) == 384" in runtime_build
    assert "COPY . " not in text  # Do not send operator secrets into the image.


def test_compose_uses_repo_root_and_exact_loopback_publication():
    text = COMPOSE.read_text()
    assert re.search(r"^services:\n  commonplace:\n", text)
    assert "context: ..\n" in text
    assert "dockerfile: deploy/commonplace/Dockerfile" in text
    ports = re.search(r"    ports:\n((?:      .+\n)+)", text)[1]
    assert ports.splitlines() == ['      - "127.0.0.1:8765:8765"']
    assert "network_mode:" not in text
    assert "privileged:" not in text


def test_compose_persists_data_limits_memory_and_loads_operator_env():
    text = COMPOSE.read_text()
    assert "- /srv/commonplace/data:/data\n" in text
    assert "- /srv/commonplace/.env\n" in text
    assert "COMMONPLACE_BASE_URL:-https://edge1.tail6cee8c.ts.net" in text
    assert "restart: unless-stopped\n" in text
    assert "mem_limit: 3g\n" in text
    assert "init: true\n" in text


def test_healthcheck_uses_python_stdlib_and_healthz_alias():
    text = COMPOSE.read_text().split("    healthcheck:\n", 1)[1]
    assert "- CMD\n        - python\n        - -c\n" in text
    assert "urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=5)" in text
    assert "assert r.status == 200" in text
    assert "curl" not in text
    assert "timeout: 10s" in text
    assert "retries: 3" in text


def test_caddy_preserves_api_paths_and_has_exclusive_static_fallback():
    active = "\n".join(line for line in CADDY.read_text().splitlines() if not line.startswith("#"))
    assert active.count("edge1.tail6cee8c.ts.net {") == 1
    paths = re.search(r"@backend path (.+)", active)[1].split()
    assert set(paths) == {
        "/app*",
        "/api/*",
        "/mcp",
        "/health",
        "/healthz",
        "/s/*",
        "/oauth/*",
        "/.well-known/*",
    }
    assert "handle_path" not in active
    backend, fallback = active.split("handle @backend {", 1)[1].split("\n\thandle {", 1)
    fallback = fallback.split("\n}\n", 1)[0]
    assert "bind 100.76.209.1" in active
    assert "reverse_proxy 127.0.0.1:8765" in backend
    assert "flush_interval -1" in backend
    assert "Content-Security-Policy" not in backend
    assert "root * /srv/commonplace/app/landing/public" in fallback
    assert "file_server" in fallback
    assert "reverse_proxy" not in fallback
    assert "Content-Security-Policy" in fallback
    assert "cmmn.dev {" not in active


def test_caddy_security_headers_and_future_short_links():
    text = CADDY.read_text()
    for header in ("X-Content-Type-Options nosniff", "Referrer-Policy no-referrer", "-Server"):
        assert header in text
    commented = "\n".join(line[2:] for line in text.splitlines() if line.startswith("# "))
    destinations = {
        "gh": "https://github.com/140er/mempalace",
        "mempalace": "https://github.com/MemPalace/mempalace",
        "install": "https://commonplace.run/#install",
        "connect": "https://commonplace.run/connect/",
        "claude": "https://commonplace.run/connect/#claude-code",
        "codex": "https://commonplace.run/connect/#codex",
        "cursor": "https://commonplace.run/connect/#cursor",
        "antigravity": "https://commonplace.run/connect/#antigravity",
        "cloud": "https://commonplace.run/connect/#cloud",
        "local": "https://commonplace.run/connect/#local",
    }
    for name, destination in destinations.items():
        assert f"@{name} path /{name} /{name}/" in commented
        assert f"redir @{name} {destination} 302" in commented
    assert "redir https://commonplace.run/ 302" in commented
    assert "app.cmmn.dev {" in commented


def test_hook_is_executable_and_uses_only_host_managed_restic():
    assert HOOK.stat().st_mode & 0o111
    text = HOOK.read_text()
    assert "set -Eeuo pipefail" in text
    assert "--env-file /srv/commonplace/.env" in text
    assert '"$repo_dir/deploy/compose.edge1.yml"' in text
    assert "exec -T commonplace commonplace backup --help" in text
    assert "stop --timeout 60 commonplace" in text
    assert '--volume "$staging_parent:/backup"' in text
    assert 'container_staging="/backup/$stamp"' in text
    assert "install -d -o 10001 -g 10001 -m 0700" in text
    assert "flock -n 9" in text
    assert 'mv -T -- "$host_staging" "$destination"' in text
    commands = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    assert not re.search(r"\brestic\s+(backup|forget|prune|init)\b", commands)


@pytest.mark.parametrize("arguments", [[], ["relative"], ["/nonexistent/commonplace-backups"]])
def test_hook_rejects_invalid_destination_with_one_failure_line(arguments):
    result = subprocess.run(["bash", str(HOOK), *arguments], capture_output=True, text=True)
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert result.stderr.startswith("commonplace backup failed: setup")


# This fake only models the external container/permission boundary. Each test
# runs the actual hook, including its traps, lock, validation and atomic move.
FAKE_DOCKER = """#!/usr/bin/python3
import json, os, pathlib, sys
args = sys.argv[1:]
if pathlib.Path(sys.argv[0]).name == 'install':
    pathlib.Path(args[-1]).mkdir(parents=True, exist_ok=True)
    sys.exit(0)
with open(os.environ['FAKE_COMMANDS'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
mode = os.environ.get('FAKE_FAIL', '')
if 'ps' in args:
    print('test-container')
elif 'exec' in args:
    if mode == 'preflight': sys.exit(9)
elif 'stop' in args:
    if mode == 'stop': sys.exit(9)
elif 'start' in args:
    if mode == 'restart': sys.exit(9)
elif 'run' in args:
    if mode == 'snapshot': sys.exit(9)
    mount = args[args.index('--volume') + 1]
    assert mount.endswith(':/backup'), mount
    destination = pathlib.Path(mount[:-len(':/backup')]) / pathlib.Path(args[-1]).name
    destination.mkdir()
    (destination / 'control.sqlite3').write_bytes(b'consistent snapshot')
    if mode != 'manifest':
        (destination / 'manifest.json').write_text('invalid' if mode == 'corrupt' else '{}')
else:
    sys.exit(10)
"""


def run_hook(tmp_path, fail=""):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    (bin_dir / "install").symlink_to(docker)
    backups = tmp_path / "backups"
    backups.mkdir()
    commands = tmp_path / "commands.jsonl"
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    env.update(FAKE_COMMANDS=str(commands), FAKE_FAIL=fail)
    result = subprocess.run(
        ["bash", str(HOOK), str(backups)], env=env, capture_output=True, text=True, timeout=10
    )
    calls = [json.loads(line) for line in commands.read_text().splitlines()]
    # Failure logs are deliberately preserved by the production hook; clean
    # up this test's log after checking the diagnostic points to a real file.
    log_match = re.search(r"details: (/tmp/commonplace-backup\.[^)]+)", result.stderr)
    if log_match:
        log_path = Path(log_match[1])
        assert log_path.is_file()
        log_path.unlink()
    return result, backups, calls


def test_hook_publishes_complete_snapshot_after_restarting_gateway(tmp_path):
    result, backups, calls = run_hook(tmp_path)
    assert result.returncode == 0, result.stderr
    snapshots = list(backups.glob("commonplace-*"))
    assert len(snapshots) == 1
    assert (snapshots[0] / "control.sqlite3").read_bytes() == b"consistent snapshot"
    assert json.loads((snapshots[0] / "manifest.json").read_text()) == {}
    assert list((backups / ".commonplace-staging").iterdir()) == []
    actions = [
        next(word for word in call if word in {"ps", "exec", "stop", "run", "start"})
        for call in calls
    ]
    assert actions == ["ps", "exec", "stop", "run", "start"]
    for call in calls:
        assert call[:4] == ["compose", "--env-file", "/srv/commonplace/.env", "-f"]
        assert Path(call[4]) == ROOT / "deploy/compose.edge1.yml"


@pytest.mark.parametrize(
    "fail", ["preflight", "stop", "snapshot", "restart", "manifest", "corrupt"]
)
def test_hook_fails_closed_and_recovers_gateway(tmp_path, fail):
    result, backups, calls = run_hook(tmp_path, fail)
    assert result.returncode != 0
    assert result.stderr.count("\n") == 1
    assert result.stderr.startswith("commonplace backup failed:")
    assert list(backups.glob("commonplace-*")) == []
    if fail == "preflight":
        assert not any("stop" in call or "run" in call for call in calls)
    else:
        assert "start" in calls[-1]
    if fail == "snapshot":
        assert "snapshot" in result.stderr
    if fail == "restart":
        assert "gateway restart failed" in result.stderr


def test_backup_shell_syntax():
    result = subprocess.run(["bash", "-n", str(HOOK)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert shlex.split(HOOK.read_text().splitlines()[0][2:]) == ["/usr/bin/env", "bash"]
