### Workflow recovery view in Project settings

  - Under the coordinator status, Project settings shows the restore state, the open findings and the steps of the project's role with the config version and lease epoch they belong to. It is read-only except for one owner action: acknowledging a restore, after a plain-language preview and typing the exact text the server shows. Cancelling sends nothing; a failure keeps the restore pending and says so.

  - Server rules are unchanged. The page never claims a lease, sends a heartbeat or stamps a result, and it does not offer voiding or reassigning a step, retiring an experiment, or acknowledging a finding (that needs the owning role's lease). Unknown states and unreadable reads are named in words, never shown as an empty list.
