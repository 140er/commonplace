"""Each fix of the Coworker page guidance must be pinned: revert it in a scratch copy and the checks fail."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECK = "deck_coworker_page_guidance.mjs"
STATES = "deck_coworker_empty_states.mjs"
MUTATIONS = {
    "status line gone": (
        "deck-coworkers.js",
        "${statusHTML(workStatus(worker, detail, checkpoint, view))}",
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
        "No run recorded for this bot yet.",
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
    "waiting count dropped": (
        "deck-coworkers.js",
        "text: waiting ? `",
        "text: false ? `",
        STATES,
    ),
    "list row back to no summary": (
        "deck-coworkers.js",
        "${e(rowSummary(row, row.key === selected ? detail : {}))}",
        "${e(displayCopy(row.summary) || 'No summary recorded')}",
        STATES,
    ),
    "conversation ignores the bot state": (
        "deck-coworkers.js",
        "state: row.state || bot?.state || 'idle'",
        "state: row.state || 'idle'",
        STATES,
    ),
    "loading shown as idle": (
        "deck-coworkers.js",
        "if (unread && view.loading)",
        "if (false)",
        STATES,
    ),
    "failed read shown as idle": (
        "deck-coworkers.js",
        "if (unread && view.failed)",
        "if (false)",
        STATES,
    ),
    "member reason dropped": (
        "deck-coworkers.js",
        " Its work in the owner's other conversations is not shared with you.",
        "",
        STATES,
    ),
    "disabled bot ignored": (
        "deck-coworkers.js",
        "if (bot.frozen) return",
        "if (false) return",
        STATES,
    ),
    "last reply dropped": ("deck-coworkers.js", "return said ? {", "return false ? {", STATES),
    "unshared card claim dropped": (
        "deck-coworkers.js",
        "'The card it worked on is not shared with you.'",
        "'No summary was recorded for that run.'",
        STATES,
    ),
    "waiting loses to an old run": (
        "deck-coworkers.js",
        "if (bot && (bot.tone === 'waiting' ||",
        "if (bot && (false ||",
        STATES,
    ),
    "board link dropped": (
        "deck-coworkers.js",
        "const boardLink = bot => bot?.board ?",
        "const boardLink = bot => false ?",
        STATES,
    ),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_mutation_is_caught(name, tmp_path):
    node = shutil.which("node")
    assert node, "Node is required"
    filename, old, new, *named = MUTATIONS[name]
    check = named[0] if named else CHECK
    scratch = Path(tempfile.mkdtemp(dir=tmp_path))
    shutil.copytree(ROOT / "commonplace/web/static", scratch / "commonplace/web/static")
    (scratch / "tests").mkdir()
    shutil.copy(ROOT / "tests" / check, scratch / "tests" / check)
    target = scratch / "commonplace/web/static" / filename
    source = target.read_text(encoding="utf-8")
    assert old in source, f"mutation anchor missing: {name}"
    target.write_text(source.replace(old, new, 1), encoding="utf-8")
    clean = subprocess.run(
        [node, "--test", str(ROOT / "tests" / check)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert clean.returncode == 0, clean.stdout[-3000:] + clean.stderr[-1000:]
    mutated = subprocess.run(
        [node, "--test", str(scratch / "tests" / check)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert mutated.returncode != 0, f"mutation survived: {name}"
