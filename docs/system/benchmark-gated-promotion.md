### Benchmark-gated promotion of an experiment config

  - A benchmark compares an accepted `exp:<name>` candidate with the live version over the same replayed tasks and stores, per task, the outcome, cost, tokens and latency. Nothing replays or calls a model in this slice; results come from the caller.

  - A candidate is promoted only when its success rate is at least live's, no task that passed on live fails on the candidate, and at least one metric is strictly better. Thresholds (minimum task count, relative gain margin, cost budget) can only tighten that rule.

  - The owner's promote writes the candidate text as a new live version and records the replaced one; rollback writes that previous text back as another new version with the identical sha256. Candidates that touch approval-gate, safety or evaluation-rule keys are refused at record and at promote time.
