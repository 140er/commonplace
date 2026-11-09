### Foreground refresh rendering

- A background workspace refresh that supersedes a pending foreground render inherits that render on the same screen.

- Navigation epochs reject stale reads and Settings completions; ordinary events leave an already rendered Settings view intact.

- Settings bounds each of its six section reads and three prerequisite reads to ten seconds, including response decoding. Failures name the section or request and offer Retry; successful reads show saved values. Orchestration settings lock contention returns retryable HTTP 503 after a one-second lock wait.

- A current request failure clears the pending render; a later background refresh retains its usual form-preservation policy.

- On Settings, a background prerequisite failure preserves already-rendered sections and mounted webhook drafts, reporting the named failure through the separate screen error with Retry. Initial and foreground failures may replace content with the error.

