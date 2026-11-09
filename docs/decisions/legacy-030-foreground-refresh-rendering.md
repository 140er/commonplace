### Foreground refresh rendering

- A background workspace refresh that supersedes a pending foreground render inherits that render on the same screen.

- Navigation epochs reject stale reads and Settings completions; ordinary events leave an already rendered Settings view intact.

- A current request failure clears the pending render; a later background refresh retains its usual form-preservation policy.

