### Account authority follows the final model

A config-driven card start selects its final model before it resolves the account and machine.
An accepted escalation to another provider therefore uses the requesting person's connection for
that provider. It never retains an earlier provider's connection or substitutes a workspace account
in personal mode.

Control settings, usable connections and candidate machine fences are captured before the runner
transaction. Inside that transaction, failed rounds and labels select the model once; the account
resolver then uses the saved control snapshot. The selected machine must pass the existing card,
project, topic, wing, runtime and requester checks before admission writes any job or ledger row.
Missing final authority refuses with the existing actionable reason. Its missing-account item is
recorded after the refused transaction, for the final provider only.

Keyed retries keep replaying their stored job under the existing request digest rules. An admitted
job retains its exact connection identity; revocation or a same-label replacement still refuses at
lease or before starting. Persistent session account checks and public identity privacy are unchanged.

The regression probe and refusal controls are in `tests/test_escalation_authority_probe.py` and
`tests/test_escalation_authority.py`.
