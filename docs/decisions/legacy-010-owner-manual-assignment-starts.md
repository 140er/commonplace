### Owner manual assignment starts

- **Manual assignments.** An owner assignment to an active agent connection claims the card immediately, including project admission and the busy-agent guard. With orchestration enabled, an assignment to the configured machine's worker requests a fresh start through the owner orchestrator path. A disabled switch, missing runner config, a different worker or a refused start leaves an explicit reason in the card's status line. Assignment never bypasses origin release. The worker can confirm its existing claim with an explicit take without a second claim event or lease renewal.

