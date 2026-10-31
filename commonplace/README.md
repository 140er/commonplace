# Commonplace gateway

One gateway in front of MemPalace: accounts, workspaces, scoped tokens, verified authorship and an audit log. Each workspace gets its own `mempalace` worker on loopback, started on first use and stopped when idle. Design: [PRODUCT-DESIGN.md](../PRODUCT-DESIGN.md) §4–7.

```bash
commonplace init --admin-email you@example.com
commonplace token add grok:coordinator --workspace personal --client grok \
  --profile slim --read-wings shared,inbox --write-wing inbox --trust untrusted
commonplace serve --host 127.0.0.1 --port 8765
```

`token add` prints the `cmmn_…` token once on stdout. Clients send it as `Authorization: Bearer cmmn_…` to `POST /mcp`. Data lives in `$COMMONPLACE_DATA` (default `~/.commonplace`): `control.sqlite3` plus `workspaces/<id>/palace`.

Social sign-in: [provider setup](SETUP.md).

Claude Code: [local setup and repeatable fresh-install check](../docs/commonplace-client-setup.md).

## Hosted plans

Set `COMMONPLACE_PLANS=on` to enable hosted plans. New social sign-ups receive Free:
250 MB storage per account and 10 minutes of server voice transcription per UTC day.
The chat-sync interval hook is every 15 minutes for Free and live for Paid; sync is not built here.
Paid has *unlimited*\*\* storage and transcription and a server-load throttle hook.

** zolang de server het aankan / as long as the server can handle it

Admins manage plans with `commonplace plan set <user email|id> free|paid|none` and inspect
usage with `commonplace plan show <user email|id>`. Payment is not built. The transcription
meter is a hook for a future endpoint. Accounts without a plan have no limits and every feature.
Unset, empty or `off` keeps plans off: self-hosted installations have no limits or plan UI.

## Context handoff

Optional handoff saves a full thread in Memory and starts a fresh agent with a pointer to it. It is installed with the gateway and off by default. Thread tools appear once a workspace enables handoff or has stored threads. Pages use the hub's web sign-in; events use its signed webhooks with the `handoff` preset. See [setup and API](../docs/handoff.md).

## Policy

| Field | Meaning |
|---|---|
| `--profile` | `slim`, `standard` or `full`; or `--tools` with an explicit list |
| `--scopes` | `memory:read`, `memory:write`, `memory:delete`, `kg:read`, `kg:write`, `events:read`, `events:write`, `private:read`, `private:write`, `admin` |
| `--read-wings` / `--write-wings` | wing names or patterns (`project_*`, `*`) |
| `--write-wing` | where an `untrusted` token's writes always land |
| `--trust` | `untrusted` (inbox writes only, no update, delete or graph writes), `standard` (no delete), `trusted` |

- `mine`, `sync`, `hook_settings` and `reconnect` are never served through the gateway.
- Wings named `private` or `private_*` also need `private:read` / `private:write`, even under `*`.
- Tools that read or change data across wings (status, graph, tunnels, events) need every wing.
- The knowledge graph has no wings, so reading it, or invalidating or superseding a fact, needs every wing. `kg:write` alone allows adding facts. Untrusted tokens never write the graph.
- A hidden drawer answers exactly like a missing one in every tool.
- A tool the token can't use is missing from `tools/list` and answers `Unknown tool`. A drawer it can't read answers `Drawer not found`.
- Searches without a wing run once per readable wing and merge by score.

The canary suite (`tests/test_commonplace_canary.py`) seeds forbidden wings and calls every MemPalace tool with scoped tokens; run it before every push.

Opt-in compact workspace storage uses a verified offline backfill and rollback. See [compact store switching](../docs/compact-store-switch.md) for backup requirements, commands and retention.
