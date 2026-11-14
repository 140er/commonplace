- **Promises:**

  - Invalid, expired and replayed email links return a token-free HTML recovery screen with Retry, without provisioning or a session cookie. Valid links still require the same confirmation and same-origin verification.
  - Email form navigations render sent and throttled recovery documents; JSON email requests keep their status and account-neutral response. Per-address email throttling is unchanged.
