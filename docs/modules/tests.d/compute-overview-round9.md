- **Owns:**

  - Round 8 of the #615 review: a matrix over every combination of liveness, load, drain, enrollment and hourly limit that asserts status, blocker and Allowed agree and uses the real lease as the oracle; browser cases that capture a runner at its hourly limit (idle and full) and the paused-tab banner.
- **Promises:**

  - No sleeps: the fake clock moves time and each check waits on a locator.
- **How it fails:**

  - A runner that says "Yes" beside a refusal, or a lease that disagrees with the answer, fails the matrix.
- **Tests:** The files above.
