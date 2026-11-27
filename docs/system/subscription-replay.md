### Subscription-only replay experiments (card #390)

The owner's 27 November decision requires subscription CLIs for every replay: Claude Code,
Codex or Grok Build. The gateway never executes a model. The local data-directory owner runs
`commonplace orch benchmark start` on their machine. Reports, accept and rollback share the
existing owner CLI, MCP and signed-in REST authorization. Agent tokens cannot accept a result,
even when their workspace role is owner.

An experiment currently evaluates **one role's prompt and workflow text**. Changes to model slots,
installed skills or routing are refused with `unsupported_replay_change` until their effects can
be replayed faithfully. The existing protected-key guard still refuses gates, safety, approval
and evaluation changes. The candidate must already be accepted on an `exp:<name>` channel;
accepting that experiment does not change live.

1. Propose the prompt change using `commonplace orch submit candidate.toml --workspace personal
   --board demo --channel exp:repair`. Accept that experiment with the existing config accept.
2. Supply a JSON manifest with `cli` (`claude`, `codex` or `grok`), optional `model`, `role`, and
   5–50 `tasks`. Each task contains `id`, a full immutable commit in `revision`, `request`, editable
   `files`, original `test_files`, a narrow `test_argv` and boolean `known_passed`. The command
   must name an original test file explicitly. All file names are repository-relative.
3. Run `commonplace orch benchmark start replay.json --repository . --workspace personal
   --board demo --channel exp:repair`. It runs live then candidate sequentially by default;
   `--concurrency 2` or `3` permits a small bounded group. Each side gets its own disposable git
   checkout. The original tests must first reproduce their known outcome. The model receives
   only the declared files and returns file contents, with tools disabled. It cannot patch tests.
4. `commonplace orch benchmark show 1 --workspace personal --board demo` shows successes out of
   the task count, token totals, elapsed milliseconds, regressions and per-side failure codes.
   Tokens come only from complete CLI JSON usage. Unknown usage stays JSON/SQL `null` with an
   explicit reason; that metric cannot count as an improvement. Cost means tokens and time.
   No currency charge is measured or displayed. The older core's optional currency field remains
   null on this path. Its nullable migration preserves every historical result transactionally.
5. Accept once with `commonplace orch benchmark promote 1 --workspace personal --board demo
   --sha256 <candidate-sha256>`. The winning decision, config versions and protected keys are
   checked again in the promotion transaction. `commonplace orch benchmark rollback --workspace
   personal --board demo` restores the replaced body and sha256 as a new live version.

The same owner actions are `orch_benchmark_get(board, replay)`,
`orch_benchmark_promote(board, replay, sha256)` and `orch_benchmark_rollback(board)` on MCP, or:

- `GET /api/orch/boards/<board>/benchmarks/<replay>`
- `POST /api/orch/boards/<board>/benchmarks/<replay>/promote` with `sha256`
- `POST /api/orch/boards/<board>/benchmarks/rollback`

The start command uses the owner's local data directory, as existing local config commands do;
this slice does not dispatch experiments from a hosted gateway to a remote machine. A hub process
has no route or tool that starts the replay worker. No new visual design is introduced.

The adapters have fixed argv and an allowlisted environment: provider keys, auth-token overrides,
proxy/base URLs, cloud-provider switches and inherited CLI settings are absent. A fresh CLI config
directory links only the owner's existing auth file; the adapter never reads, copies or logs it.
Claude must report a first-party subscription login, Codex must report ChatGPT login and is forced
to that mode, and Grok must successfully query its signed-in plan with `usage --json`. Missing,
API-only or unverifiable login refuses before inference. No login is attempted. Grok versions
without the plan query refuse safely. Its [CLI reference](https://docs.x.ai/build/cli/reference)
and [configuration reference](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/05-configuration.md)
describe the headless flags and config isolation contract.

A CLI plan-limit response ends the experiment as **paused: plan limit**, without retry or API
fallback. Explicit `commonplace orch benchmark resume 1 --repository . --workspace personal
--board demo` retains completed sides and repeats only unfinished work, against the same manifest
and versions. Already active workers finish before the pause returns. A crash/interruption is a
refusal, not an automatic repeat of an uncertain model call. All workers and subprocess pipes
finish before database teardown. Processes have no total runtime cutoff: only 15 minutes without
output, a real error or an output/resource limit stops them. The existing machine memory probe
refuses starts when available memory is known to be below 8192 MB. Raw CLI/test output is discarded;
only bounded, fixed failure reasons and numeric results are persisted.
