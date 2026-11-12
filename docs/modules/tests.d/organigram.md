- **Owns:**

  - `tests/test_orch_organigram.py` covers reporting lines (valid tree, cycle, unknown parent), escalation to the role a step's owner reports to, reassignment only along the line, owner-only kinds (including `needs_person` steps) and a config without lines; `tests/spaces_surfaces.json` carries `orch_reassign_step`.
