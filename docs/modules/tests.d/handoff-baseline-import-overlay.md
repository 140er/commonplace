- **Promises:** The pinned handoff baseline in `tests/test_handoff_adversarial_off_perf.py`
  carries over, by module-level import, every module the overlaid board, rules and
  projects code needs and the baseline lacks, so a new import on develop cannot break
  it. Modules the baseline has stay pinned and extension packages stay inert.
- **How it fails:** A pinned baseline that cannot import fails every comparison case at
  once with the child's own stderr; its replies carry a 120 second deadline, so a wedged
  child is killed instead of awaited.
- **Tests:** `test_baseline_carries_modules_new_board_imports_need` pins the carry-over
  rules; the comparison cases in the same file run against the carried snapshot.
