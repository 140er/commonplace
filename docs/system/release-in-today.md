### Cards waiting for release stay in Today

- `needs_me` lists every restricted-origin card waiting for release in `approvals` with `approval_kind` `origin` for the workspace owner, whether or not a Coworker is connected. When a live board coordinator could release it, the same card is listed with a reason saying so; the owner's release permission is unchanged and stays server-side (`task_approve`).

- A card read of a card waiting for release carries `creator`: `{name, trust, inbox_only}`. `trust` is the creator connection's trust level (`null` when the connection is gone) and `inbox_only` says it can write only to the inbox.

- Members and agents without release authority do not get the item or the Release block.
