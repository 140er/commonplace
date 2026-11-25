### One readiness checklist for project and bot setup

Project settings and bot pages share a Ready to start? checklist. Each prerequisite
has a Done, Needs you or Not needed state, a reason and one owner action. Members
see the same states with an instruction to ask the project owner, without setup
actions. The projection includes no key values, key references, tokens, machine
identifiers or connection identifiers.

The chat model and its key are checked independently using the existing local or
hosted configuration and key allowlist. An inherited hosted model still needs its
project opt-in. Personal chat runs on the gateway, so it does not require an agent
machine, project connection or machine trust. Project starts show runner enrollment,
first use of the agent connection, machine binding approval and, where enabled,
project start permission. A registered runner does not promise current capacity or
connectivity: admission checks these again when starting work. Notifications remain
optional. These states explain prerequisites and do not replace start admission.

The checklist reuses the Coworker recovery wording and existing settings actions.
Missing machines lead to Add machine in Admin; machine approval leads to Admin,
and project start permission leads to Project settings. Model and key actions
focus Chat answers and reveal its setup controls on phones; connection actions
open the existing connect flow, and permission actions open its disclosure. The rows remain visible
when a connection disappears. Unknown state codes keep the existing escaped setup
guidance rather than leaving an empty row. The layout wraps on a phone; the bot
conversation keeps its composer reachable while the checklist can scroll.

The desktop and phone fixture journeys cover both interfaces and compare the
rendered strings at 1280 and 390. They use synthetic state and assert no external
model or physical machine operation. Separate API fixtures exercise the real state
projection for owners and members.
