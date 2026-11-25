- **Owns:**

  - Round 6 of the #615 review: one test per runner status for "Allowed to start card runs"; node cases for a clock-aged row's mark and next step and for a filter reset; browser cases that capture a turned-off, removed, offline and never-connected runner, the clock-Offline row, and the 320 px owner workspace detail (button inside the screen, 44px tall).
- **Promises:**

  - No sleeps: the fake clock moves time and each check waits on a locator.
- **How it fails:**

  - A runner that says "Yes" while it is turned off or removed, or a clock-aged row with no mark, fails these tests.
- **Tests:** The files above.
