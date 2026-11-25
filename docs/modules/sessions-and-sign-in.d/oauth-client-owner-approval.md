- **Promises:**

  - Public OAuth registrations start unapproved in every workspace. The existing consent form offers an unticked client approval to current signed-in owners only; each workspace/client pair is checked and recorded inside the code transaction, and exchange rechecks the connection's workspace. Members can authorize only approved clients. Session validity and live membership are rechecked before issuance.

  - Existing registrations are never silently approved. Existing access/refresh grants retain their expiry, rotation, scope and revocation behavior, while outstanding codes and new authorizations require owner approval. Approval does not grant any optional scope or person capability.
