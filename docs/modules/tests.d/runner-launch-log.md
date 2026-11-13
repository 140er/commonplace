- **Tests:**

  - `tests/test_runner_agent.py` holds the launch log: a refusing bridge's output is kept per job in a 0600 file with the grant and long token-like strings redacted, and a tool error's reason (`runtime_mode_escalation_denied`) is readable there; `tests/runner_fake_t3.py` has a `leaky` mode that echoes the credential on stderr.
  - `tests/test_runner_launch_detail.py` pins the previously missing readable cause on the job, both stream tails under the hard cap, shared secret redaction before persistence, replay and result validation, owner/board-person reads and agent/foreign-workspace refusals, deletion with the job and no new successful-job output. The bridge double's flood mode emits synthetic credentials on both streams.
  - The diagnostic row is exported only with its owner's workspace and removed by workspace deletion. `tests/test_redact.py` covers redaction of a private-key block before retaining a long diagnostic tail, while ordinary redaction keeps its input bound.
