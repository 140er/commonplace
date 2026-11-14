### Run ledger

- A run is one runner start. Its ledger row (`run_ledger` in `runner.sqlite3`) is written in the start's own transaction and follows its state; the table is additive and older starts get a row when the store opens.

- The hub never measures, prices or estimates usage. Tokens, cost (millionths of a currency unit plus the currency), the log pointer and the run's end are only what the run's own enabled machine reported through `POST /api/runner/usage`; an unreported value stays NULL and reads "not reported".

- A reported end changes neither the start job nor machine capacity nor the card (D3 stays open).
