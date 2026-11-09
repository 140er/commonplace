### Rules-injection test clock

- Rules-injection functional tests run on a frozen deadline clock and a slow-host regression re-runs them with a clock that exhausts the budget on every read; the production 80 ms budget and its fail-open empty text are unchanged.
