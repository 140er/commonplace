### Rules initialization deadline

- Owner summary-edit checks use a fixed injection clock and a forced-expiry regression, retaining verbatim injection and acceptance assertions without changing the production deadline.

- Shared-proposal acceptance is checked with a fixed injection clock; scheduler-delay regression coverage retains exact rule text and spacing for both agents without changing the production fail-open deadline.

- Rules initialization deliberately returns the legacy response when its deadline expires, even after rendering; the 500-rule incomplete marker is checked with a fixed clock, separately from the unchanged informational 25 ms added p95 budget.

