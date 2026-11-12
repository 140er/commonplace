- **Tests:**

  - `tests/test_runner_agent.py` holds the launch log: a refusing bridge's output is kept per job in a 0600 file with the grant and long token-like strings redacted, and a tool error's reason (`runtime_mode_escalation_denied`) is readable there; `tests/runner_fake_t3.py` has a `leaky` mode that echoes the credential on stderr.
