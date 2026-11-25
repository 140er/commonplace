- **Tests:**

  - `tests/test_guards_identity.py` covers the placeholder matcher, pre-push rejection, amend/rebase error text, and hook wiring. `tests/test_guards_hooks.py` exercises real pushes, lint/format/type failures, and preservation of the previous hook's arguments and stdin with acceptable fixture identities.
