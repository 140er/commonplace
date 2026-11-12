### Batch merge rule (tests are never the bottleneck)

- A PR into `develop` merges on a review/gate PASS plus its affected tests on the exact head; the full suite runs per batch on the `develop` tip (about every five merges or every hour, one run at a time), is bisected to one PR on red, and merges into `develop` hold only while it is red. Global changes (lock and build files, `tests/conftest.py`, migrations) still run the full suite per PR. The rule lives in `CLAUDE.md` and is pinned by `tests/test_agent_docs_batch_rule.py`.
