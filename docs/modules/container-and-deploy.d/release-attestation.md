- **Promises:**

  - Deployment binds the reviewed source commit, build context and image identity, waits for readiness, and verifies running package identity and internal service health.

- **Tests:**

  - Mocked deployment regressions reject stale release pins, missing runtime identity, invalid service mappings and failed readiness without a real container daemon.
