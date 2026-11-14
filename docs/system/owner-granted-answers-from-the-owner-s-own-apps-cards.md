### Owner-granted answers from the owner's own apps (`cards:decide`)

- Answering a Needs-me ask stays a signed-in owner action by default. A separate,
  unticked `cards:decide` consent lets one of the owner's own connections answer
  asks addressed to that owner, over MCP `decide` or REST
  `POST /api/cards/<n>/decide`.
- Granting: the owner's browser session only. PATs get it at creation
  (`"decide": true`) or through `PATCH /api/connections/<id>`; OAuth clients get it
  only when the owner ticks the consent box, and the request must also carry
  `memory:read` and `memory:write`. Members see an owner-only notice and a forged
  tick grants nothing. Authorization-server metadata advertises the scope; OAuth
  defaults never include it. The control layer takes the signed-in owner's
  principal, never a bare user id: `create_token` needs it as `grantor` for its
  own top-level, non-seat connection; `set_connection_decide` needs it as actor
  and refuses runner (its runner message), inactive, child, agent-started and
  seat connections with specific 409 messages; OAuth code minting rechecks a
  person owner. The CLI refuses it; person policies, profile defaults and seat or
  runner policies never include it. The grant route also refuses any current
  board coordinator (checked in the board store). Settings → Agents offers the
  grant only on the viewing owner's own eligible, unexpired, non-coordinator
  PATs, with the consent text served from one source. Withdrawing works on any
  connection, also expired or revoked ones; another member's row gets its own
  message.
- Answering: the connection must be a live PAT or OAuth connection of a current
  owner, top-level, not a runner, hold the scope, and the ask's recipient must be
  exactly that owner. Project seats and board coordinators are refused. The app
  needs `memory:write` and read access to the board's wing; write access is the
  owner's **person-level** access (`_owner_writes`: the owner's current person
  policy can write the board's wing), so it answers wherever the owner could in
  the browser, regardless of the app's own `inbox` write wing, and is refused
  where the owner cannot write. It cannot escalate or answer its own ask. An untrusted PAT with the scope sees `decide`; without it,
  tool lists stay unchanged.
- Audit: inside the answer's board transaction, before it commits, every
  delegated answer writes `audit_log` action `card.decide.delegated` with user,
  connection id, auth kind, board and card number. A failed audit write fails the
  answer. The audit log is append-only and a separate database, so if the board
  transaction rolls back after the row was written, a `card.decide.rolled_back`
  row with `reason=audit_seq=<seq>` cancels it. Window: an audit row without an answer is possible after a kill between the two commits, or when the cancel write fails. No answer commits without its audit row; no
  reconcile exists yet. Grants and withdrawals write
  `connection.decide.grant` and `connection.decide.revoke`.
- Revocation: `PATCH {"decide": false}` (PAT or OAuth) or revoking the connection
  takes effect on the next request; policies load on every request and lose the
  scope when the holder stops being owner. Losing eligibility removes the stored
  grant, each with a `connection.decide.revoke` audit row, so regaining it needs
  fresh consent:
  - demotion: a membership trigger fires on an `UPDATE` of `memberships.role`
    from `owner` to anything else (reason "ownership lost"); deleting a
    membership is refused by the foreign key while the user has connections;
  - coordinator assignment: `board_set` clears the grant of the connection it
    assigns (reason "coordinator") inside its transaction and again after the
    commit; the grant route re-reads coordinators after writing a grant and
    withdraws it (409) if the app became one meanwhile. Unassigning does not
    restore it, because every path that removes or replaces a coordinator
    (`board_set` replace or unassign, and the auto-unbind when the owner runs the
    own-wing action and the app can no longer coordinate the board, whether or not
    the wing changes) clears that connection's grant before the board commit. The
    clear is its own control-database commit, ordered before the board COMMIT, not
    one atomic transaction with it: a grant left by a failed clear or a crash is
    gone before the app stops coordinating; a failed clear rolls the removal back;
    a crash after the clear leaves the coordinator in place without a grant (fail
    closed);
  - expiry: rotating an expired PAT clears the grant (reason "expired"); rotating
    a live PAT keeps it. Expired tokens can still be rotated, as the connection
    docs describe.
- OAuth token and refresh responses (and the stored token rows) carry only the
  granted scopes the connection still holds after policy and role intersection,
  so a withdrawn `cards:decide` never reappears in a refresh.

- Test collection: the consent and Agents browser test skips without Playwright
  (collecting `tests/` must never error); `CP_BROWSER_E2E=1` makes a missing
  Playwright fail instead.

- Compatibility baseline: the pinned handoff "off" baseline test supplies
  `commonplace/decide_grant.py` and `mempalace/deadline.py` (leaf modules the current
  board and rules code import);
  its gateway, policy and handoff comparison stays pinned.
