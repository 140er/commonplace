### Subscription replay acceptance, 27 November 2026

One real experiment used the signed-in Claude Code subscription CLI (`claude -p`, model alias
`haiku`) on a throwaway local data directory and test board. The five past-task snapshots were
seeded from `tests/test_subscription_replay.py::historical_tasks`, with known failing Python
checks. This proves the integration; it makes no claim about production-task quality.

The failing pattern was a builder prompt that preserved defective source unchanged. One
candidate prompt on `exp:repair` instructed the builder to repair the implementation. Both
sides received identical task snapshots and original tests; ten model calls ran sequentially.

| Measurement | Live | Candidate |
| --- | ---: | ---: |
| Real tests passed | 0/5 | 5/5 |
| CLI-reported tokens | 17,122 | 15,565 |
| Total elapsed milliseconds | 34,636 | 26,882 |
| Regressions | — | 0 |

Cost is tokens and elapsed time. No currency estimate was used. Token totals include the
CLI's reported input, output and cache buckets; elapsed time includes checkout, auth and tests.

Sanitized log excerpt (full numeric capture: `docs/evidence/subscription-replay/acceptance.json`):

```text
model_calls: 10
live: passed=0/5, tokens=17122, latency_ms=34636
candidate: passed=5/5, tokens=15565, latency_ms=26882
regressions: []
GET /healthz: 200 {"ok": true}
anonymous report: 401
owner report: 200
owner promote: 200
owner rollback: 200
rollback restored original body and sha256: true
throwaway gateway and workers stopped before database close
```

The fake-binary tests cover all three CLI adapters and the resumable plan-limit path. They
never call a real model. No real Codex or Grok inference was used for this acceptance run.
