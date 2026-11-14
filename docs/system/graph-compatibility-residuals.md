### Graph compatibility residuals

- Accepted parity exception: non-string tunnel labels other than null remain
  ineligible on authenticated list/follow/count paths, although legacy local reads
  can list those records. Null and absent labels remain readable and stored intact.
- The existing explicit-follow path predates graph projection: unknown arguments
  can still become an empty list instead of a JSON-RPC invalid-params error.
  This remains a separate follow-up; graph-tool validation does not change it.
  the public follow schema does not expose that argument. Tunnel label reference
  handling predates this change and remains follow-up work for graph traversal.
