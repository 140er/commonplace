- **Owns:**

  - Records saved without a credential and binding one afterwards: `create_gateway` and `create_node` accept `credential_id` and `credential_version` together or not at all (`control.py`), store `null` for both when left out and report `credential_bound`; `Store.bind_credential` and `POST /api/compute/gateways/<id>/credential` or `/nodes/<id>/credential` (`routes.py`) record a reference, once, on such a record.
- **Promises:**

  - An unbound gateway or target is refused for verification, proposals, scripts and passive collection with the fixed reason `credential_not_bound` (409), enforced in `Store._enrollment` and `Store._route`, the one place every binding is built, so no caller can skip it. The reference is validated like one given at creation and nothing is opened or verified when binding.
  - A record that already has a reference is never changed (`credential_already_bound`, 409). Owner-only access, foreign-ID concealment, the exact field set and the no-secret rule are the same as for the other writes, and existing records and manual reference entry behave as before.
- **How it fails:**

  - Half a reference, an invalid reference or an unknown field is `invalid_request`; a missing record is `not_found`; a non-owner is `owner_required`; rebinding is `credential_already_bound`; using an unbound record is `credential_not_bound`.
- **Tests:** `tests/test_compute_optional_credentials.py`.
