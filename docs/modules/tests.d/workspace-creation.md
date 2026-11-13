- **Tests:**

  - Workspace creation has HTTP, browser-free Node and optional real-browser coverage. The browser file skips cleanly when browser checks are disabled or the dependency/executable is absent. Both looks cover cancellation, creation, refusal and switching; the HTTP tests additionally cover isolation, authority, policy, plans, durable request replay and limits.
