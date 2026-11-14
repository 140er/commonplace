"""Each fix of the Coworker page guidance must be pinned: revert it in a scratch copy and the checks fail."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECK = "deck_coworker_page_guidance.mjs"
MUTATIONS = {
    "status line gone": (
        "deck-coworkers.js",
        "${statusHTML(workStatus(worker, detail, checkpoint))}",
        "",
    ),
    "running job not recognised": (
        "deck-coworkers.js",
        "const RUN_NOW = ['pending', 'running'];",
        "const RUN_NOW = [];",
    ),
    "card link dropped": ("deck-coworkers.js", "const href = number ?", "const href = false ?"),
    "idle sentence dropped": (
        "deck-coworkers.js",
        "Nothing is running and no earlier work is recorded.",
        "No work recorded yet.",
    ),
    "unavailable progress leaks": (
        "deck-coworkers.js",
        "job?.task_progress?.availability === 'available' ? job.task_progress : null",
        "job?.task_progress ?? null",
    ),
    "send enabled without a conversation": (
        "deck-coworkers.js",
        "${!enabled || busy || !text.trim() || block ? ' disabled' : ''}>${busy ?",
        "${busy || !text.trim() ? ' disabled' : ''}>${busy ?",
    ),
    "no visible reason": (
        "deck-coworkers.js",
        "Send is off until a conversation is open. <a",
        "<a",
    ),
    "steer not blocked": (
        "deck-coworkers.js",
        "if (mode === 'steer' && !canSteer(detail)) return",
        "if (false) return",
    ),
    "list opens another page": (
        "deck-coworkers.js",
        'href="${botPage(bot.id, bot.coworkerOnly)}"',
        'href="#/bots/${enc(bot.id)}/coworkers"',
    ),
    "rail opens another page": (
        "deck-shell.js",
        "botPage(bot.id, bot.shellKind !== 'personal')",
        "bot.shellKind === 'personal' ? `#/bots/${enc(bot.id)}` : '#/bots/-/coworkers'",
    ),
    "mode lines dropped": (
        "deck-coworkers.js",
        "${Object.values(MODE_HELP).map(line => `<li>${e(line)}</li>`).join('')}",
        "",
    ),
    "private line dropped": ("deck-coworkers.js", "  private: 'Private:", "  privat: 'Private:"),
    "no next step for a missing model": (
        "bot-setup.js",
        "Next step: open a project's settings, go to Chat answers, then pick a local model or a hosted provider with its key.",
        "",
    ),
    "model next step missing on the page": (
        "deck-coworkers.js",
        '${setup ? `<p class="cb-setup"',
        '${false ? `<p class="cb-setup"',
    ),
    "row next step missing": (
        "deck-coworkers.js",
        "setup && /no chat model/i.test(summary)",
        "false",
    ),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_mutation_is_caught(name, tmp_path):
    node = shutil.which("node")
    assert node, "Node is required"
    filename, old, new = MUTATIONS[name]
    scratch = Path(tempfile.mkdtemp(dir=tmp_path))
    shutil.copytree(ROOT / "commonplace/web/static", scratch / "commonplace/web/static")
    (scratch / "tests").mkdir()
    shutil.copy(ROOT / "tests" / CHECK, scratch / "tests" / CHECK)
    target = scratch / "commonplace/web/static" / filename
    source = target.read_text(encoding="utf-8")
    assert old in source, f"mutation anchor missing: {name}"
    target.write_text(source.replace(old, new, 1), encoding="utf-8")
    clean = subprocess.run(
        [node, "--test", str(ROOT / "tests" / CHECK)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert clean.returncode == 0, clean.stdout[-3000:] + clean.stderr[-1000:]
    mutated = subprocess.run(
        [node, "--test", str(scratch / "tests" / CHECK)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert mutated.returncode != 0, f"mutation survived: {name}"
