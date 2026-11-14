### Lease refusals say what is missing

A seat made in Project settings can authenticate and read its board, but a lease claim
(`orch_resume` and the other lease writes) is refused until a workspace owner has done
the setup: accept a Coworker config with a `[runner]` table (machine, project,
runtime mode) and `limits.max_agents`, trust the seat, and put it on a machine's
requester list. Without the board's own trusted seat, a board with no `[runner]` config
refuses with `not_configured`; an untrusted or unlisted requester gets `not_allowed`.

Both refusals keep their code and status and now say which prerequisite is missing. The
refusal's `details` hold `missing` (stable slugs such as `accepted_config`,
`runner_table`, `seat_not_trusted`, `not_on_requester_list`) and `fix`, the owner's route:
Project settings > Coworker start permissions > Trust and allow starts for trust and
requester entries, and `orch_config_propose` / `orch_config_accept`, the board's configs
REST routes or the owner CLI `accept` for the config. The text names only the caller's own
board and seat; it does not change who may claim.
