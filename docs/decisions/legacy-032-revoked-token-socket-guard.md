### Revoked-token socket guard

- The real-socket revoked-token guard records the server's completed 401 and asserts that neither body reads nor route dispatch occurred.

- A forced early close covers upload failure independently of whether the client can read the response.

- The gateway already sends `Connection: close`; this test does not add body draining to the unauthenticated request path.

