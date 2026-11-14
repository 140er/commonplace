### Settings load harness dependencies

The Settings load and retry harness extracts the production shell refresh function.
That function closes over the needs receipt ledger and the roster helper, so the
harness supplies the real ledger factory and the production roster helper, as the
other refresh harnesses do. A missing binding would make every case fail with a
reference error before the asserted deadline, error or recovery behaviour runs.
Product behaviour is unchanged; only the test context was completed.
