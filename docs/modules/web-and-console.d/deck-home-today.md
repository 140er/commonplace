- **Owns:**

  - `deck-home.js` owns the undimmed `#/` hero (`#/home` is an alias). It reuses the bot chat editor, send control and SSE sender. Explicitly selecting an existing project coworker conversation uses that conversation's existing message route. Bot choices respect the chat permission and lifecycle fields. Context, model and effort controls appear only when the hub supplies them; additive coworker turn fields enable model and effort overrides from the configured provider's catalog. No provider is enabled or selected implicitly.

  - The last three submitted runs display their own stream actions or returned job progress. Refresh binds a bot reply to its original message and a coworker job to its returned id. Missing step data produces a plain state, never inferred steps. Slash opens the shell command palette; mentions choose from the live bot list.

  - `deck-today-capy.js` reads the existing personal needs queue, bot roster and authorized event feed. Cards expose only versioned actions offered by the queue. Approve and decide requests keep the normal session routes and carry a stable Idempotency-Key on retry. A successful answer updates the shared queue count, records Activity, and retains the exiting card for 280ms. A person-scoped receipt ledger prevents an older read from restoring a settled count. A changed version remains visible. Hold sends an offered hold choice when one exists; otherwise it keeps the card pending for this visit. Reply uses the decision form or opens the existing card when a decision reply is unavailable.

- **Promises:**

  - Desktop shortcuts are A/H/R and J/K. Editors, dialogs, modifier keys, repeats and the single-key preference retain their existing meaning. Phone cards clamp body copy, hide facts, and place Reply and Turn down in More. Activity stacks below the main list on narrow screens. All new styles use Capy tokens and are scoped to Deck; the classic controllers and look remain available.
