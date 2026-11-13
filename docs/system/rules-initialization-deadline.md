### Rules initialization deadline

- The read deadline lives in the stdlib-only `mempalace/deadline.py`; `commonplace.rules.checks` wraps it with its own clock, and the fact readers import the leaf instead of rules. Behaviour is unchanged. The reason is test selection: a function-level import of rules from the knowledge graph made nearly every change reach most test files, which pushed quick per-PR checks to the full suite.

- Owner summary-edit checks use a fixed injection clock and a forced-expiry regression, retaining verbatim injection and acceptance assertions without changing the production deadline.

- Rules-injection functional tests run on a frozen deadline clock and a slow-host regression re-runs them with a clock that exhausts the budget on every read; the production 80 ms budget and its fail-open empty text are unchanged.
- Onboarding tests that assert startup content opt into the same frozen clock and run the installed startup hook with only its 0.4 s hub allowance lifted; a slow-hub regression re-runs them with every gateway POST slower than that allowance. The production budgets and fail-open notices are unchanged.
- Shared-proposal acceptance is checked with a fixed injection clock; scheduler-delay regression coverage retains exact rule text and spacing for both agents without changing the production fail-open deadline.

- Rules initialization deliberately returns the legacy response when its deadline expires, even after rendering; the 500-rule incomplete marker is checked with a fixed clock, separately from the unchanged informational 25 ms added p95 budget.
