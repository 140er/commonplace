- **Owns:**

  - Saving a Compute gateway or target without a credential and binding one afterwards from the same screen (`compute.js`, `compute.css`): the form says in plain words that the credential is bound to the record's ID after it is saved; the list and the details show "No credential bound yet" (and "Credential bound" afterwards); a "Bind a credential" form offers the listed credentials of the right kind or the copyable step filled with the record's own IDs.
- **Promises:**

  - The browser never claims a record is usable on its own: the server refusal reason is shown in plain words, a credential state that is neither a reference nor empty shows "Credential state unavailable" instead of a blank, and no key or secret is typed, shown or sent. Existing records and the manual reference fields work as before.
  - The copy button treats a missing clipboard, or a copy that does not return a promise, as a failed copy and shows the fallback text instead of "Copied.".
- **Tests:** the `unbound` case of `tests/compute_ui.mjs`, run by `tests/test_compute_ui.py`.
