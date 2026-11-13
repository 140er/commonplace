### Cold sign-in and recovery

- Signed-out app documents, assets and provider inventory have a bounded read budget independent of authentication. Cold loading cannot spend the sign-in allowance. Credential, anonymous API and per-email abuse limits remain enforced with their existing capacities and refill periods; public-route classification is unchanged.
- Email sign-in keeps loading, sent, throttled and failed requests in the app with recovery. Expired email links and throttled browser navigations return token-free HTML recovery documents, preserving error status, no-store/security headers and Retry-After where applicable. Invalid links never provision an account or issue a session.
