### Gateway vector diagnostics

The gateway normalizes worker tool payloads and protocol error data before any
public or internal consumer receives them. A true `vector_disabled` flag uses the
shared reason `Vector index unavailable; repair required.`; a reason without that
exact true flag is omitted. Direct and fan-out search, duplicate detection and
status share this rule. Reconnect diagnostics remain inaccessible because the
gateway refuses reconnect. Protocol write-refusal data has no disabled flag, so
its raw reason is omitted. Other response fields and stored text are unchanged.
Workspace owners who read every wing keep the raw reason behind a true disabled
flag, since nothing private is at stake for them; members and tokens scoped
below the whole workspace, owner-issued ones included, stay on the fixed reason.
Local single-user MCP callers retain the original diagnostic detail. The global
disabled boolean itself remains an accepted diagnostic, not a visible-row count.
