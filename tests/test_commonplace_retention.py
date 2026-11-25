"""Post-deploy retention (deploy/commonplace/retention.sh) against a fake docker."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RETENTION = ROOT / "deploy" / "commonplace" / "retention.sh"

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="retention.sh targets GNU/Linux hosts"
)

FAKE_DOCKER = r"""#!/usr/bin/env bash
set -eu
state=$HOME/images.json
printf '%s\n' "$*" >> "$HOME/docker.log"
case "$1 ${2:-}" in
"inspect -f")
    [[ -n "${RUNNING_ID:-}" ]] || exit 1
    case "$3" in
    "{{.Config.Image}}") echo "$RUNNING_REF" ;;
    "{{.Image}}") echo "$RUNNING_ID" ;;
    esac ;;
"image inspect")
    jq -er --arg ref "$5" --arg fmt "$4" '.[] | select(.tag == $ref or .id == $ref)
        | if $fmt == "{{.Id}}" then .id else .rev end' "$state" | head -n1 ;;
"image ls")
    jq -r '.[] | [.created, .tag, .id] | @tsv' "$state" ;;
"image rm")
    jq --arg ref "$3" 'map(select(.tag != $ref))' "$state" > "$state.tmp" && mv "$state.tmp" "$state" ;;
"builder prune") : ;;
*) exit 97 ;;
esac
"""


def sha(n):
    return f"{n:040x}"


def image_id(n):
    return "sha256:" + f"{n:064x}"


def setup(tmp_path, images, running=None, compose_pin=None, releases=()):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "id").write_text("#!/usr/bin/env bash\necho 0\n")
    for name in ("docker", "id"):
        (bin_dir / name).chmod(0o755)
    (tmp_path / "images.json").write_text(json.dumps(images))
    compose = tmp_path / "compose.yml"
    compose.write_text(
        f"services:\n  commonplace:\n    image: {compose_pin}\n"
        if compose_pin
        else "services: {}\n"
    )
    rel = tmp_path / "releases"
    rel.mkdir()
    for i, name in enumerate(releases):
        (rel / name).mkdir()
        os.utime(rel / name, (1_000_000 + i, 1_000_000 + i))
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "COMMONPLACE_ROOT": str(tmp_path),
        "COMPOSE_FILE": str(compose),
        "DEPLOY_RELEASES_DIR": str(rel),
    }
    if running:
        env["RUNNING_REF"], env["RUNNING_ID"] = running
    return env


def run(env, **extra):
    env = {**env, **extra}
    return subprocess.run(
        ["bash", str(RETENTION)], env=env, capture_output=True, text=True, timeout=30
    )


def tags(tmp_path):
    return {i["tag"] for i in json.loads((tmp_path / "images.json").read_text())}


def history(count):
    """develop-<n> built at time n; rollback-before-<n> is the image that ran before n."""
    images = []
    for n in range(1, count + 1):
        created = f"2026-11-{n:02d} 10:00:00 +0100 CET"
        images.append(
            {
                "tag": f"commonplace:develop-{sha(n)[-12:]}",
                "id": image_id(n),
                "created": created,
                "rev": sha(n),
            }
        )
        if n > 1:
            images.append(
                {
                    "tag": f"commonplace:rollback-before-{sha(n)[-12:]}",
                    "id": image_id(n - 1),
                    "created": f"2026-11-{n - 1:02d} 10:00:00 +0100 CET",
                    "rev": sha(n - 1),
                }
            )
    images.append(
        {
            "tag": "halo-relay:latest",
            "id": image_id(999),
            "created": "2026-11-01 10:00:00 +0100 CET",
            "rev": "",
        }
    )
    return images


def test_keeps_newest_per_kind_and_protected_images(tmp_path):
    images = history(9)
    # Running an older release (a rollback) must still protect it and its rollback.
    running = (f"commonplace:develop-{sha(2)[-12:]}", image_id(2))
    env = setup(
        tmp_path, images, running=running, compose_pin=f"commonplace:develop-{sha(2)[-12:]}"
    )
    result = run(env, DEPLOY_RETENTION_IMAGES="3")
    assert result.returncode == 0, result.stderr
    left = tags(tmp_path)
    for n in (7, 8, 9):
        assert f"commonplace:develop-{sha(n)[-12:]}" in left
        assert f"commonplace:rollback-before-{sha(n)[-12:]}" in left
    assert f"commonplace:develop-{sha(2)[-12:]}" in left  # running + pinned
    assert f"commonplace:rollback-before-{sha(2)[-12:]}" in left  # its rollback (image 1)
    assert f"commonplace:develop-{sha(1)[-12:]}" in left  # same image as that rollback
    assert f"commonplace:develop-{sha(4)[-12:]}" not in left
    assert f"commonplace:rollback-before-{sha(5)[-12:]}" not in left
    assert "halo-relay:latest" in left
    log = (tmp_path / "docker.log").read_text()
    assert "builder prune -f --filter until=24h" in log
    removals = [line for line in log.splitlines() if line.startswith("image rm")]
    assert removals
    assert all(re.fullmatch(r"image rm commonplace:\S+", line) for line in removals)


def test_releases_keep_newest_and_protected_contexts(tmp_path):
    names = [sha(n) for n in range(1, 13)] + [sha(3) + ".stale-20261119-103214"]
    running = (f"commonplace:develop-{sha(1)[-12:]}", image_id(1))
    env = setup(tmp_path, history(12), running=running, releases=names)
    result = run(env, DEPLOY_RETENTION_RELEASES="4")
    assert result.returncode == 0, result.stderr
    left = {p.name for p in (tmp_path / "releases").iterdir()}
    assert left == {sha(1)} | {names[-1]} | {sha(n) for n in (10, 11, 12)}


def test_dry_run_removes_nothing(tmp_path):
    images = history(9)
    env = setup(
        tmp_path,
        images,
        running=(f"commonplace:develop-{sha(9)[-12:]}", image_id(9)),
        releases=[sha(n) for n in range(1, 13)],
    )
    result = run(
        env,
        DEPLOY_RETENTION_IMAGES="1",
        DEPLOY_RETENTION_RELEASES="1",
        DEPLOY_RETENTION_DRY_RUN="1",
    )
    assert result.returncode == 0, result.stderr
    assert "dry-run: docker image rm" in result.stdout
    assert tags(tmp_path) == {i["tag"] for i in images}
    assert len(list((tmp_path / "releases").iterdir())) == 12


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"DEPLOY_RETENTION_IMAGES": "0"},
        {"DEPLOY_RETENTION_RELEASES": "x"},
        {"DEPLOY_BUILDER_PRUNE_UNTIL": "1d; rm"},
    ],
)
def test_fails_safe_without_running_image_or_with_bad_config(tmp_path, extra):
    images = history(9)
    running = None if not extra else (f"commonplace:develop-{sha(9)[-12:]}", image_id(9))
    env = setup(tmp_path, images, running=running, releases=[sha(n) for n in range(1, 13)])
    result = (
        run(env, DEPLOY_RETENTION_IMAGES="1", **extra)
        if "DEPLOY_RETENTION_IMAGES" not in extra
        else run(env, **extra)
    )
    assert result.returncode == 0
    assert "nothing removed" in result.stderr
    assert tags(tmp_path) == {i["tag"] for i in images}
    assert len(list((tmp_path / "releases").iterdir())) == 12


def test_skips_while_deploy_lock_is_held(tmp_path):
    images = history(9)
    env = setup(tmp_path, images, running=(f"commonplace:develop-{sha(9)[-12:]}", image_id(9)))
    with open(tmp_path / ".deploy.lock", "w") as lock:
        import fcntl

        fcntl.flock(lock, fcntl.LOCK_EX)
        result = run(env, DEPLOY_RETENTION_IMAGES="1")
    assert result.returncode == 0
    assert "a deploy is running" in result.stderr
    assert tags(tmp_path) == {i["tag"] for i in images}
