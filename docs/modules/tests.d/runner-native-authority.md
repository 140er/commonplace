- **Owns:**

  - The native authority design's evidence boundary and existing fail-closed regression references.

- **Promises:**

  - Source inspection and CLI discovery are reported separately from native grant enforcement, signed live status, provider authorization and a productive run.

- **How it fails:**

  - Existing synthetic tests do not count as actual issuer or native enforcement proof. The unimplemented live acceptance steps remain explicitly blocked.

- **Tests:**

  - tests/test_docs_obligations.py, tests/test_docs_fragments.py, tests/test_commonplace_public_surface.py, tests/test_eval_guards.py.
