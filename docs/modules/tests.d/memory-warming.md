- **Tests:**

  - `tests/memory_warming.mjs` answers the notes search with a fake 503 in both Memory looks and settles on the rendered page, not on fixed sleeps. It covers the warming and busy lines, the countdown, the three-retry limit and its request count, the 10 s cap, the fallback with its Retry button, results and the typed query staying on screen, a new query and leaving Memory cancelling the retry, one request in flight at a time, and a 503 without a hint never being retried.
