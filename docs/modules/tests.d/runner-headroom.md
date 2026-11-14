- **Tests:**

  - `tests/test_agent_limit_388.py` checks the 1–100 schema and project bounds, legacy table widening, machine capacity and memory refusal. Its positive start fixture supplies the launch reserve as well as the configured memory floor and fixes the CPU probe for deterministic memory checks.

  - `tests/test_agent_limit_surfaces_388.py` exercises 100 accepted and 101 rejected through both REST and MCP config proposal paths.

  - `tests/test_runner_headroom_388.py` covers CPU probing, finite numeric reports, memory and CPU pre-spawn refusals, independent clients competing for a shared reservation, reservation expiry and unknown probes.

  - `tests/test_runner_capacity_contract_388.py` proves machine-only resource reporting, low-CPU lease refusal, the high project limit remaining bounded by runner capacity, and both capacity fields in the board summary. Its public reporting contract fails on the pinned base before the endpoint exists.

  - `tests/test_runner_headroom_compat_388.py` keeps normal-limit session launches available under low CPU or report transport failures, permits unknown probes without reservation storage, and preserves known-shortage refusals for expanded limits. Existing session regression, board-loss, lifecycle and authority tests remain unchanged.
