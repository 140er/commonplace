- **Owns:**

  - Guided machine pairing (`pairing.py`, `pairing_routes.py`, design in `docs/system/compute-enroll.md`): `machine_pairings` and `machine_pairing_steps` in the control database link one owner request to one runner enrollment and add the owner's single accept, a closed reason and the steps the machine reports. Owner REST under `/api/compute/pairings`, the machine's own `POST /api/runner/pairing-status`, and the code-authenticated `POST /api/runner-pairing/check`.

- **Promises:**

  - Only the signed-in workspace owner creates, accepts or closes a pairing, and that check runs before any body is read; members read a view without code, fingerprint, grants or ids; agent tokens get nothing. Every refusal carries a fixed `reason` (and the `field` when one field is at fault) that has a sentence and a next step in `REASONS`.
  - The one-time code is 256 random bits, lives 15 minutes, is stored only as its hash and appears in the creation response and nowhere else (not in later reads, a replay, the audit log, the pairing tables or logs). Creating is idempotent by `request_id`, rate limited, and capped at five open setups. The code is spent only by the existing exchange; `check` never spends it.
  - The state of a pairing (`waiting`, `connected`, `approved`, `ready`, `offline`, `failed`, `expired`, `cancelled`, `declined`, `revoked`) is derived in one function from the enrollment, the owner's accept and the machine's reports. `ready` means approved, all four steps reported `ok` and heard from within 15 minutes; it never means a Coworker can run, because the accept only enables the machine and a machine still runs work only when it is also attested.
  - Accept and close are compare-and-set on `version`, idempotent by `request_id`, and commit with their audit rows in one transaction. Accept needs the identity digest the owner was shown, so a changed machine key or grant is `identity_changed`. Close uses the existing invalidation, so credentials, recovery and consent requests end together.
  - A copied (`copy_from`) or explicit grant is validated again for the current owner, including every requester; nothing is inherited from a revoked source.

- **How it fails:**

  - A stale, used, expired, cancelled, revoked or foreign-workspace code is answered by `check` with its own reason to the holder of the pairing id only; the exchange keeps its uniform refusal. Step reports are facts for the owner's page and grant no authority; a malformed, unknown-cause or closed-pairing report stores nothing.

- **Tests:** `tests/test_compute_pairing.py`.
