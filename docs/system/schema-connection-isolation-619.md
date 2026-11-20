### Plan-schema checks serialize with background transactions

The plan-schema test uses a control-database transaction for each invalid insert, so it acquires the shared connection's lock and rolls back the rejected write. The unknown-plan, missing-user and API-level rejection assertions remain in place. A synchronized regression runs those same checks against background database traffic and observes the real SQL and lock ownership.
