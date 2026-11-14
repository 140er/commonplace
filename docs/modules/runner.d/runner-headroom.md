- **Owns:**

  - `headroom.py` probes available memory and CPU and reserves startup headroom in the private local journal. `capacity.py` stores numeric machine reports and bounds card leases by the current machine grant, hourly allowance and resource headroom.

- **Promises:**

  - Resource reports add no launch authority. Only accepted config limits above 20 enable the added headroom check on card starts and Coworker session launches or continuations; short-lived reservations serialize startup bursts across clients sharing the machine journal.

  - Lease payloads carry the pinned accepted agent limit only when it exceeds 20; absent or normal limits never opt into the additional guard. Unknown probes do not require reservation storage, and reporting is best effort while credential revocation remains fatal.

- **How it fails:**

  - Insufficient memory or CPU refuses a local start with `low_memory` or `low_cpu`; unavailable reservation accounting refuses with `busy`. Unknown probes preserve existing behavior. Stale reports are unknown and do not supply a resource refusal. Reservations reduce startup overload risk without imposing process memory limits.

- **Tests:**

  - `tests/test_runner_headroom_388.py` covers resource validation, local refusals, concurrent reservations and expiry. `tests/test_runner_capacity_contract_388.py` covers machine-only reporting and lease limits; `tests/test_agent_limit_388.py` protects the memory guard and machine cap.

- **Error reporting:**

  - One poll pass prints each distinct hub error once: when the best-effort resource report and the following lease or session call fail the same way, the runner prints a single `hub unavailable` or `hub refused` line, not one per failed call. `tests/test_runner_agent.py::test_k2_hub_errors` covers it.
