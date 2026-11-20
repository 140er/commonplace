- **Owns:**

  - `overview.py` puts the setup-only case into the one "can this runner take new work" answer (`can_take_new_work`): a machine added through the setup guide has the single reason `setup_only` ("This setup only checks the machine. Coworkers do not run on it in this version."), so its chip, sentence, blockers and "Allowed to start card runs" ("No: this setup only checks the machine") agree and it never reads offline, never-connected or not approved. A cloud workspace beside only such machines says so (`ONLY_RUNNERS_SENTENCE`) instead of "No machine is set up yet".

- **Promises:**

  - Attesting a setup-only machine does not turn the answer into "Yes": the install is setup-only whatever its binding says. A heartbeat makes it an ordinary runner row. The Not approved reason names one step in one place (Needs me for a guided machine, Admin's Attest otherwise) and counts as needing attention; "Allowed" names what holds a full runner's place; a turned-off runner says there is no control to turn it on again.

- **How it fails:**

  - A hub without the pairing tables has no setup-only machines; every row reads as before.

- **Tests:** `tests/test_compute_pairing.py` (blockers, intake and silence after 20 minutes, attested case, the workspace sentence).
