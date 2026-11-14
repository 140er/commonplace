- **Owns:**

  - Project settings show Coworker config proposals with Changes, Accept and Reject for owners; members can read versions and changes without decision or release controls.
- **Promises:**

  - Reject asks for confirmation, names the version and explains that the current config stays in use. Decision results show the actor; completed decisions remove proposal actions even if a refresh fails. Existing config REST routes enforce owner authority and audit decisions.
  - Deck resolves config actors from the signed-in workspace directory, uses "you" for the viewer and retains an unknown actor's identifier. Version labels and actor details have separate lines; diffs wrap inside a neutral well, confirmations use the same well, and results stay beside the versions. Classic output is preserved.
- **Tests:**

  - `tests/test_project_config_review.py` drives the Node controls and gateway rejection, audit and member refusals. `tests/test_commonplace_project_review_browser.py` exercises acceptance, confirmed rejection and release in the browser, including both looks for rejection.
