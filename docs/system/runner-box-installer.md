### Runner box installer proposal

- `docs/design/runner-box-installer.md` covers cards #449/#450 under #430: short-lived single-use enrollment codes, private credential delivery, approved acknowledgement fetch, headless runtime setup and browser-only login/grant decisions. Six independently mergeable slices remain off by default. The second runner box survey is read-only; no runtime behavior or machine setting changed in this documentation PR.

### Enrollment and private recovery (slice 1)

- **M:** Setup remains off with no settings row. The owner-only backend exposes
  `GET/PUT /api/runners/install-settings`, `POST /api/runners/enrollments` and
  `GET/DELETE /api/runners/enrollments/<id>`. Only an explicit signed-in-owner
  settings write can opt in; off reads and every existing token see 404. No UI,
  installer command, MCP tool, capability, event or catalog entry is added.
- **M:** Issuance freezes a validated machine grant and its manifest digest under
  an owner request id. The code has 256 random bits and five minutes of validity.
  Only its hash is stored. The initial response alone carries the code; repeated
  requests return status, changed intent conflicts, and a lost code needs reissue.
- **M:** `POST /api/runner-enrollment/exchange` accepts code authority only, with
  workspace, label, digest, installation id and RSA-3072/4096 public key. A single
  control transaction conditionally consumes the code, creates one disabled,
  unbound machine with separate credentials, encrypts delivery and audits. No
  service, attestation, approval or execution authority is activated.
- **M:** `POST /api/runner-enrollment/recover` issues a one-use 60-second server
  challenge for a matching enrollment/key/installation. Recovery or acknowledgement
  requires an RSA-PSS-SHA256 signature over `<action>:<challenge>`; recovery returns
  the original RSA-OAEP-SHA256/AES-256-GCM ciphertext within ten minutes, without
  issuing another machine or credential. Acknowledgement and expiry erase it.
- **M:** Only the exact exchange and recovery POST paths permit requests without
  a session or bearer credential: exchange authenticates the single-use code, and
  recovery authenticates the bound key through a single-use server challenge.
  Machine credentials cannot be required before their first delivery or after
  that response is lost. Both paths return 404 for disabled setup. The shared
  public-route set used by five boundary guards explicitly names both paths,
  with no wildcard or prefix exception and strict set equality retained.
- **M:** Enabled-workspace bootstrap attempts have durable per-operation counters
  and a ten-attempt/minute ceiling. Invalid, expired and replayed codes share a
  refusal body; audit records ids and fixed outcomes. Disabling setup invalidates
  pending codes and deliveries and revokes both credentials created by this flow.
  Existing manual machines keep their own controls. Per-enrollment and ordinary
  machine revocation also purge recovery. Re-enabling restores no old authority.
- **M:** Four control tables hold settings, enrollments, deliveries and attempt
  counters; account classification includes workspace scope and delivery-parent
  ordering. Export redacts secret hashes and encrypted delivery. A worker created
  only after opt-in sweeps expiry every minute; every authority use also checks
  expiry transactionally. `tests/test_runner_enrollment.py` supplies the proof.
- **T:** Signed owner manifests/receipts, supported single-accept installation,
  browser login/grants and supervised stop/lease proofs remain later-slice work.
  This slice supplies no ready mode or installer/runtime compatibility claim.
