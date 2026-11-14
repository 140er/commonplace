- **Owns:**

  - The no-progress nudge (`stall.py`) reads each card's restricted-origin flag and proposal status, and treats owner-only cards as work that is not the orchestrator's to move.
- **Promises:**

  - A doing card of restricted origin, or one whose proposal is still pending, is never counted as actionable, doing or expired, whoever its assignee is. It cannot make a board stalled, cannot trigger a nudge and cannot keep the nudge streak alive. Lease and claim logic is unchanged, and a board file that predates the two columns is read without them.
- **How it fails:**

  - An ordinary expired claim still stalls the board; only owner-only cards are skipped.
