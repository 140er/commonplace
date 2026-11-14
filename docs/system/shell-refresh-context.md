### Shell refresh harness dependencies

The shell creates its needs receipt ledger during module startup for both looks.
Refresh uses it to reconcile late queue snapshots after a decision and keeps the
authorized bot roster for navigation. Node harnesses that extract refresh must
supply those same dependencies as they do the API client and shell state.

The integration contexts use the real ledger factory and the production roster
helper. A production fallback for an omitted test-context binding would conceal
an incomplete harness and could weaken stale-read protection. The settled-needs
case exercises that protection through the extracted refresh function, including
new card versions and a workspace change.
