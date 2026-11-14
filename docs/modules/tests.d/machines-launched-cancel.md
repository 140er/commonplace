- **Tests:**

  - `tests/test_bots_machines.py` follows the confirmed-stop contract for a launched agent: cancelling it records a stop request and answers `started`, never `cancelled`, until the runner confirms the stop; without a retained run id the stop stays unsupported, with one it stays pending and a repeat request keeps the same stop request; only a start that is already cancelled or failed is refused plainly as finished, and an unknown start as not found.
