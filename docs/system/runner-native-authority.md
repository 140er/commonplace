### Native runner authority prerequisite

- **M:** [Native authority design](../runner-native-authority.md) separates the
  independently pinned host issuer, hub approval and machine enrollment. The
  native dispatch/provider boundary must implement the required issuer protocol;
  an adapter preflight cannot supply native grant enforcement.
- **T:** Owner-selected custody with effective job/key separation and a managed
  native-host implementation route are prerequisites. Version 1 receipts remain
  setup-only. No live key, account approval, native proof or execution capability
  is supplied by the design; artifact delivery cannot clear that fence.
