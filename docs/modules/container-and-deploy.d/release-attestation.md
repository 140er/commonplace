- **Promises:**

  - Deployment waits for Compose readiness (up to 90 seconds); when a requested build commit is configured it also verifies that the running package reports exactly that commit, otherwise it reports the attestation as not requested.
