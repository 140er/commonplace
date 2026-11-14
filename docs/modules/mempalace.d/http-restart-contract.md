- **Owns:**

  - HTTP startup propagates initial bind/lease status, logs supervised recovery transitions, retains children across marker publication failures and treats live unknown-identity PIDs as orphans.

  - HTTPMonitor owns one child, endpoint, startup/reap and cross-generation cooldown; Coordinator owns generation admission through response flush. Neither opens a palace in the monitor or retains request data there.

- **Promises:**

  - CLI search/mine refuse managed gaps before capability or local routing. Older Windows runtimes retain the bounded fact channel without set_blocking. HTTP idle exits report idle and error replies do not advance successful-completion accounting.

  - Full HTTP and proxy delegation supervise once; Commonplace passes internal child mode with two private inherited pipes. Registry markers survive child cleanup and prevent local fallback during restarting, cooldown and orphan draining. Count/RSS retirement and a transport-body concurrency limit remain disabled.

- **How it fails:**

  - Three startup failures, unexpected deaths or confirmed early RSS retirements latch for 60 monotonic seconds. The standalone monitor probes once per cooldown; live orphan PID/start identities block launch and are rechecked every 60 seconds. Accepted calls drain on parent EOF; a hung call stays unavailable rather than being force-killed.

- **Tests:**

  - Restart review regressions cover actual pre-fix entrypoints, marker retries, nonlocking signal handlers, unknown identities and real startup exit statuses.

  - Generation restart, private-pipe lifecycle, deterministic monitor circuit/orphan tests and short fixture-only real-process restart/EOF smoke cover containment, not production allocation attribution.
