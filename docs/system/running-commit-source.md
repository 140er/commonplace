### The running commit comes from the build marker, with an environment fallback

- The live deploy payload reported `commit: null`, `commit_source: "unknown"`. The image recipe was right: it writes the build argument into a package marker. The deploy script never set that argument unless the operator filled in an optional setting, so the marker was empty.
- Decision: the deploy script now derives the commit from the staged source tree when none is configured (the archive stager records the commit a git archive carries) and attests it like an explicit one. The app also accepts `COMMONPLACE_COMMIT` as a second source, used only when the build marker is empty or invalid, and names the source it used (`build`, `env`, `unknown`).
- Not changed: a valid build marker still wins, an invalid value from either source is ignored and never echoed, and the app never reads git at runtime.
