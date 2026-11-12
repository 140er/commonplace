### Release verification

- Deployment waits (bounded) for Compose readiness. When `COMMONPLACE_BUILD_COMMIT` is configured it fails unless the running package commit equals it; when unset it reports "not requested" and does not fail, so deployments that pin source and image by other means are unaffected. Operator configuration remains separate.
