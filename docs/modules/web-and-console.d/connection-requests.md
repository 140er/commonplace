- **Owns:**

  - Connections loads `/api/connection-requests` for a member and keeps the existing owner connection controls. Members can request by name or purpose, add a note, refresh status, cancel a pending request, collect an approved token once, or revoke it.
  - Both Waiting on you presentations render the member's connection request with Approve and Decline, plus an optional decline reason. Their shared queue count and decision ledger include requests and keep stale reads from restoring a decided item.
- **Promises:**

  - Direct connection creation stays owner-only. Approved member connections show the member's name in the owner's list; rotation cannot reveal their secret to the owner. Member secret collection uses the existing copy-and-dismiss region.
