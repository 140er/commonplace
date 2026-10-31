# MemPalace fork: Codex and Claude Code rerun

Fork: [140er/mempalace](https://github.com/140er/mempalace), final `develop` at `2b18634e` (PRs #1–#5 merged). Rerun build: `develop` at `eb229c3b` (PRs #1 and #2; #3–#5 don't change ingest or recall behaviour, and #5's Codex eval reran the recall questions on the final plugin). Trial 1 (MemPalace 3.11.0, 2026-10-29): the first trial report.
Rerun harness: a separate evaluation workspace (same prompts, models, scoring and contamination guards as trial 1).

## Verdict

| Agent | Trial 1 (3.11.0) | Rerun (fork) |
|---|---|---|
| **Claude Code** +MP | 6/8. Never searched on "anything I should know" / "safe way to" questions | **8/8**. Searched on every memory question and answered Q4/Q5 from memory |
| **Codex** +MP | 2/8 as shipped: auto-save filed 0 drawers, then "palace empty, I don't know" | **8/8** with a palace filled only by its own hooks from real Codex 0.163 rollouts (23 drawers) |
| **Codex**, empty palace | 2/8 (below its 3/8 baseline) | **3.5/8**: when memory has nothing it reads the code, so it no longer drops below baseline |

Baselines are unchanged (Claude 3.5, Codex 3), so the gains come from MemPalace, not from model drift. Both agents now get every memory-only fact right (Q4, Q5, the Q7 reasons, the Q2 rationale, Julius's veto) and neither invents the Q8 decision. The recall instructions are shorter than before (the skill by 30%), and recall triggers without any command in Claude Code and Codex.

## What changed in the fork

| PR | Fix | Review | Fix round | Merged |
|---|---|---|---|---|
| [#1](https://github.com/140er/mempalace/pull/1) fix(codex) | Codex 0.163+ rollouts carry turns only as `event_msg/item_completed` UserMessage/AgentMessage items. Parser, hook exchange counter and checkpoint extractor read both forms; wing from `session_meta.payload.cwd` (was `sessions`) | Reviewer: 2 medium, 2 low | All but one fixed: T3 worktree cwds collapse to the project wing; a rollout resumed across the upgrade keeps every turn (drop only a cross-form repeat of the role's last turn); Codex background-command notices are boilerplate. Not fixed (pre-existing): see gaps | `da92e423` |
| [#2](https://github.com/140er/mempalace/pull/2) fix(recall) | Recall skill fires on "anything I should know about X" / "safe way to do Y" / gotchas and before "I don't know"; with nothing in memory it answers from the repo, labelled, never from model memory; code or a commit is not a decision | Reviewer: no blocking, 5 wording | All 5 applied | `eb229c3b` |
| [#3](https://github.com/140er/mempalace/pull/3) fix(init) | `init --yes` and non-tty init never prompt (mine only with `--auto-mine`); `.gitignore` edited only with `--update-gitignore`; neutral default halls for new palaces | Reviewer: 2 medium, 3 low, 1 nit | All fixed: an existing palace with no config.json keeps the legacy halls; the init skill asks before adding .gitignore entries; website docs; messages; hint only when entries are missing | `cfeb3864` |
| [#4](https://github.com/140er/mempalace/pull/4) docs(codex) | Nested Codex hooks format everywhere; project trust and hook review documented; read-only trust warning in `mempalace status` | Reviewer: 4 medium, 2 low | All fixed: leftover flat example in hooks/README; linked worktrees use the main repo's trust; no false warning from ~/.codex outside a repo; Windows case-insensitive trust keys; site anchors; deleted cwd | `d615a14b` |
| [#5](https://github.com/140er/mempalace/pull/5) feat(recall) | Product decisions: automatic recall proven in Codex (eval, 5/5 with no command), optional `recall` command in all three plugins, new wording in the Cursor and Antigravity copies (each shorter) | Reviewer: no blocking; 2 medium, 4 low | All applied: generic skill lists `recall`; eval installs the plugin with hooks off (palace untouched: 23 drawers before and after), read-only sandbox by default, exec failures exit 2; always-on description trimmed; per-file size budgets pinned at current sizes | `2b18634e` |

All are upstream-friendly (no fork-only changes). The rerun build (`eb229c3b`) had #1 and #2. #3 and #4 don't change ingest or recall. On final develop `d615a14b`, `init --yes` neither prompts nor hangs on an open, silent stdin, and it leaves `.gitignore` untouched (it prints a hint).

## Instruction token cost

Rule: recall instruction text may only shrink. Every recall file is now at or under its old size, and `tests/test_recall_auto_trigger.py` pins each one at its current size.

| Text (when it is loaded) | Before | After |
|---|---|---|
| Recall skill description (every session's skill list, Claude and Codex) | 410 chars, ~103 tokens | 366 chars, ~92 tokens |
| Recall SKILL.md (when it fires) | 6848 chars, ~1712 tokens | 4781 chars, ~1195 tokens |
| Antigravity recall skill (when it fires) | 6278 chars | 4918 chars |
| Cursor always-on rule (every turn, if installed) | 1119 chars, ~280 tokens | 959 chars, ~240 tokens |
| Cursor plugin rule / example rule / Antigravity rule | 1168 / 1063 / 874 chars | 1113 / 1008 / 835 chars |
| Optional `recall` command (new, PR #5) | — | Codex ~40 tokens per session (one `source-command-mempalace-recall` line); Claude ~25 tokens (command listing) |

Token figures are estimates (chars / 4). No line was added to CLAUDE.md or AGENTS.md: the skill change alone made Claude search on Q4/Q5.

## Scores

Same 8 questions, prompts and models as trial 1 (Claude Code 2.1.x with `claude-sonnet-5-5`, Codex CLI 0.163.0 with `gpt-6.1-sol` at medium effort). Each question runs in a fresh session; auto-save is off during recall. Pass criteria were written down before reading any answer (the evaluation plan). Answers: the evaluation artifacts.

| # | Question (needs) | Claude base | Claude +MP (old → **new**) | Codex base | Codex +MP (old → **new**) | Codex empty palace (old → **new**) |
|---|---|---|---|---|---|---|
| 1 | Pin field + rejected alternative | 0.5 | 1 → **1** | 0.5 | 0 → **1** | 0 → **0.5** (`pinnedAt` from code) |
| 2 | Why server not client | 0.5 (flagged inference) | 1 → **1** | 0.5 | 0.5 → **1** | 0.5 → **0.5** (repo docs, flagged) |
| 3 | Who vetoed a table, where state goes | 0 | 1 → **1** | 0.5 | 0 → **1** | 0 → **0.5** (where, from code) |
| 4 | Port 5733 on the evaluation host | 0 | 0 → **1** | 0 | 0 → **1** | 0 → **0** |
| 5 | Safe typecheck, and why | 0 | 0 → **1** | 0 | 0 → **1** | 0 → **0** |
| 6 | How worktree ports are chosen (code) | 1 | 1 → **1** | 1 | 1 → **1** | 1 → **1** |
| 7 | Uncommitted fix, why no test, branch | 0.5 | 1 → **1** | 0.5 | 0.5 → **1** | 0.5 → **0.5** (fix from diff) |
| 8 | Pin shortcut decision (control: none made) | 1 | 1 → **1** | **0**: invented "We chose Cmd+Shift+P" | 0 → **1**\* | 0 → **0.5**\*\* |
| | **Total /8** | **3.5** (trial 1: 3.5) | 6 → **8** | **3** (trial 1: 3) | 2 → **8** | 2 → **3.5** |

Trial 1's "Codex +MP" column is the as-shipped run (0 drawers). Trial 1 also scored 8/8 with a hand-converted palace; this rerun reaches 8/8 with no hand work.

\* "I found no earlier shortcut decision in memory. The repo's commit records Mod+Shift+P…". It reports what a commit says and claims no decision, the same answer type that earned Claude its 1. A strict reading of the pre-registered rule gives 0.5, so Codex +MP is 7.5 or 8.
\*\* "I don't know what we decided… the repo search didn't reveal a recorded decision." Correct substance, but phrased as "don't know", so 0.5.

Notable behaviour:
- **Claude searched the palace on 7/8 questions** (trial 1: 5/8): every memory question, plus Q8. It skipped only Q6, the code question, which it answered from `scripts/dev-runner.ts`. On Q4/Q5 it quoted the stored warning verbatim.
- **Codex read the skill and searched on every question in both MemPalace arms.** With an empty palace it widened once, said "No palace found" in one line, and answered from the repo. In trial 1 it stopped at "palace empty".
- **Q8:** Claude base (but not Codex base) treats the existing `mod+shift+p` binding as code, not a decision. With MemPalace, both agents get it right.

## Cost per recall question

Averages over 8 questions. Token counts include cache reads.

| Arm | Wall (trial 1 → rerun) | Input tokens | Tool calls | Spend (8 questions) |
|---|---|---|---|---|
| Claude base | 16.2 → 21.8 s | 127k → 137k | 2.2 → 2.5 | $0.98 → $1.02 |
| Claude +MP | 16.2 → 16.6 s | 121k → 147k | 2.8 → 3.8 | $0.85 → $0.87 |
| Codex base | 19.8 → 18.1 s | 104k → 107k | 3.0 → 3.4 | — |
| Codex +MP | 24.2 → 18.1 s (empty, as shipped) | 162k → 119k | 5.5 → 3.6 | — |
| Codex empty palace | 24.2 → 20.5 s | 162k → 149k | 5.5 → 5.5 | — |

MemPalace is still about cost-neutral. Claude +MP is faster and cheaper than Claude base, because one search replaces a git-log hunt. It reads about 10k more input tokens, from loading the skill on every question (it now fires every time) plus the MCP schema lookup. The Claude base wall time rose because a few base runs went on long git-log searches (Q2 base: 53 s).

## Tests and CI

No GitHub Actions; local checks (full suite, ruff) per PR, plus uncapped full-suite runs.

| PR | local checks | Full suite |
|---|---|---|
| #1 | First run at `a92376b1`: FAIL. pytest hit "Too many open files" at 94%, then failures cascaded (see below). Rerun at `37b7c451`: 30 min step timeout at 85%, no failures before the cutoff | Uncapped at `37b7c451` (ulimit 65536): **7807 passed, 73 skipped, 0 failed** (33m51s) |
| #2 | 30 min step timeout, no failures before the cutoff | Post-merge set on develop `eb229c3b` (every SKILL.md / recall-protocol / readme-claims test, plus normalize, hooks_cli, convo_miner): **652 passed, 1 skipped** |
| #3 | `6b2967dd`: **PASS**, 7825 passed, 75 skipped (36m45s, step timeout 60m) | |
| #4 | `d79db318`: 7834 passed, **1 failed**: `tests/test_dsh_plugin.py::test_dsh_plugin_node_suite` ("hooks run one at a time per session", a Node timing check). #4 does not touch the dsh plugin, the test passes 3/3 on the PR head alone and passed in #3's run, so it is a load flake. Merged under the project's policy (unrelated failures don't block) | |
| #5 | `87bd89cc`: **PASS**, 7861 passed, 75 skipped (30m55s). Codex eval `scripts/evals/codex_auto_recall.sh`: 5/5 searched with no command, palace and `~/.codex` untouched | |

The suite took about 34–37 min under shared load, exceeding the configured 30-minute step limit. #3, #4 and #5 ran with a 60-minute step timeout.

### Separate item: "Too many open files" in the full suite

- Seen once: local checks on #1 at `a92376b1` (Python 3.13, CI=1, ulimit 65536). It hit EMFILE at about 94% (test_sync.py onwards), then crashed in pytest's tmpdir cleanup.
- Not reproduced on: develop `84582fb0` uncapped (Python 3.12, CI=1, ulimit 65536), **7797 passed**, peak 1848 open fds; #1 at `37b7c451` uncapped, 7807 passed, peak 2148 fds; later local runs.
- Real but small leak, pre-existing on develop: `tests/test_sync.py` alone goes from 28 to about 1300–1550 open fds (same under 3.12 and 3.13 with CI=1). It is spread over many tests; the largest single step is +70 in `TestUnresolvedSources::test_the_report_does_not_depend_on_the_order_drawers_arrive_in`. That is about 2% of a 65536 limit, so it does not explain the one EMFILE run by itself. The likeliest extra factor is that run sharing the host with other suites, but this is not proven.
- Not caused by #1: its diff opens no files (`with open` only, in already-existing readers).
- Follow-up (not done here): close the palace/backend handles the test_sync fixtures open (likely sqlite/chroma clients created per hypothesis example), and add an fd-count assertion in a session-finish hook so a leak fails loudly instead of cascading.

## Known gaps (not fixed here)

- **Forked sub-agent rollouts never parse** (pre-existing). 34 of 453 rollouts on the evaluation host have two `session_meta` lines (`forked_from_id`), one AgentMessage and no UserMessage. They fail the two-message gate, so `mempalace mine ~/.codex/sessions` warns about them on every run and never marks them done.
- **Codex ignores `.codex-plugin/skills/*`** (pre-existing). Codex 0.163 builds this plugin's command skills from the root `commands/*.md` (the Cursor commands) as `source-command-mempalace-*`. The Codex-specific `$mempalace <command>` skills in `.codex-plugin/skills/` are never loaded, and their descriptions differ from the Cursor ones.
- **Flaky dsh timing test.** `tests/test_dsh_plugin.py::test_dsh_plugin_node_suite` ("hooks run one at a time per session") failed once under load (two suites sharing the host) and passes alone.

## Recorded product decisions

1. **How Codex users get the recall skill.** The project chose B: an optional `recall` command in all three plugins, with recall triggering automatically, never needing a command. **Correction:** the premise in my first version of this section was wrong. Codex 0.163 loads the plugin from the repo root (marketplace `"path": "./"`, manifest `"skills": "./skills/"`), so the Codex plugin has always listed `skills/mempalace-recall` in every session. The trial copied it into `.agents/skills` only because the trial harness didn't install the plugin. Codex never reads `.codex-plugin/skills/*`; it turns the root `commands/*.md` into skills instead. PR #5 proves the automatic path with a real `codex exec` eval: 5/5 searched with no command. It adds the optional command as a thin wrapper (~40 tokens per Codex session). The recall description itself costs ~100 tokens per Codex session, as before.
2. **Antigravity and Cursor recall copies.** The project chose B: port the new trigger and repo-fallback wording. Done in PR #5. Every file is shorter than before.
