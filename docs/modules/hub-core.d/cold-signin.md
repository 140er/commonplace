- **Promises:**

  - Anonymous app-shell GETs (documents, assets and provider inventory) use a separate bounded 600/minute per-source budget. Credential routes retain both 30/minute anonymous and 10/minute credential budgets; per-email limits are unchanged. Other anonymous requests and short links keep their existing policies. No route gains public access.
  - Throttled browser navigations to sign-in routes retain HTTP 429 and Retry-After, with a readable token-free recovery screen. JSON clients keep the existing error contract.
