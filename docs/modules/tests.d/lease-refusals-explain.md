- **Tests:**

  - `tests/test_projects_seat_lease.py` proves each lease refusal (no config, no `[runner]`, wrong seat role, seat of another project, untrusted seat, unlisted requester, revoked machine) names the missing prerequisite and where to fix it with the same code and status, leaks no other credential, and that `requester_ok` equals "no gaps" for owners, listed, unlisted and untrusted agents.
