- **Owns:**

  - `overview.py` words a cloud workspace nothing has started like the cloud workspaces list does: status code `not_started`, label "Saved, not started", quiet tone, with or without a machine (`SAVED_LABEL`, `NOT_STARTED_NOTE`, `NO_MACHINES_SENTENCE`, compared with `compute-workspaces.js` by a test). A missing machine is never an attention state; an unreadable saved image is one (`image_unreadable`). The instruction "Choose a machine for it with Change settings" is given only when a target machine exists; with none, the sentence and the blocker say a machine has to be set up first, and "No machine chosen" is retired in favour of "Machine: not chosen yet".
- **Promises:**

  - The overview and the list never describe one record in two ways; a workspace with no machine is listed and counted but does not "need attention".
- **How it fails:**

  - A record whose image cannot be read shows "Image unreadable" and counts as needing attention instead of failing the overview.
- **Tests:** `tests/test_compute_overview.py` (vocabulary, machine and no-machine wording, unreadable image, the string comparison with the script).
