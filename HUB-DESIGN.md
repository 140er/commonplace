# One shared memory hub for multiple AI clients

Design note, 2026-10-29. Research only, nothing built. Items marked **(verify)** are client details I'm not sure are current; step 1 checks them.

## Short answer

Yes, mostly. Every client that matters can reach one remote MCP server over streamable HTTP. The fork already has most of the server side: `mempalace serve` runs one palace behind an HTTP MCP endpoint (`POST /mcp`) with a bearer token, TLS, Host pinning, a read-only mode, and a rule that proxied traffic must carry a token (`mempalace/mcp_server/http.py`, `mempalace/cli/parser.py:581`).

Three things are missing for this use:

1. **OAuth.** claude.ai and ChatGPT connectors want OAuth (or no auth at all). The hub only does a static bearer token. Grok Bot, Claude Code, Codex and Cursor can send the token as a header.
2. **Per-client scopes.** Any client with the token sees every wing and every tool, including `mempalace_mine` (reads paths on the server's disk) and the delete tools. That has to change before health data and cloud clients share a server.
3. **Trusted provenance.** `added_by` on `mempalace_add_drawer` is whatever the client sends. The server should stamp who wrote each memory.

## 1. Which clients can connect

| Client | Remote MCP (HTTP) | Local stdio | Auth it can do | Reaches the hub over |
|---|---|---|---|---|
| Claude Code (CLI and IDE sessions) | Yes, `claude mcp add --transport http <url> --header "Authorization: Bearer …"` | Yes | Header token or OAuth | Tailscale |
| Claude desktop, web and mobile (claude.ai) | Yes, custom connectors. Add once on the web; desktop and mobile pick them up | Desktop only | OAuth or no auth. No static header field **(verify)** | Public URL. Anthropic's cloud makes the calls, so it can't use the tailnet |
| Codex CLI / IDE | Yes, `[mcp_servers.x] url = …` with `bearer_token_env_var`, or `codex mcp login` for OAuth | Yes | Header token or OAuth | Tailscale |
| ChatGPT (web, desktop, mobile) | Yes, connectors in developer mode. Plan-dependent **(verify)** | No | OAuth or no auth **(verify)** | Public URL |
| Cursor | Yes (streamable HTTP and SSE), `headers` in `mcp.json` | Yes | Header token or OAuth | Tailscale |
| Grok Bot | Yes, connector by URL (per brief) | No | OAuth or header token | Public URL. Not on the tailnet |
| Kimi | Kimi CLI has MCP config **(verify)**. Web app: no custom MCP that I know of **(verify)** | CLI only | Unknown | Tailscale for the CLI |

Two notes:

- The hub answers `POST /mcp` with plain JSON and has no `GET /mcp` stream or `Mcp-Session-Id`. The streamable HTTP spec allows that, but some clients may expect SSE. Step 1 tests each client.
- Clients that only do stdio can still reach the hub. The fork's stdio-to-hub forwarder (`mempalace/mcp_server/runtime.py:38`) or a generic `mcp-remote` bridge covers them.

## 2. Architecture

```
 tailnet clients                                   cloud clients
 (Claude Code, Codex, Cursor,                      (Grok Bot, claude.ai,
  coding-agent sessions)                      ChatGPT)
        │  tailscale serve (HTTPS, tailnet only)          │  public HTTPS (Funnel or tunnel)
        ▼                                                 ▼
 ┌──────────────── Memory server, Docker ───────────────────────────┐
 │  mempalace serve  --host 127.0.0.1 --port 8765                     │
 │   - one process owns the palace (ChromaDB + SQLite KG)             │
 │   - token -> client identity, allowed wings, allowed tools (new)   │
 │  palace on local storage, nightly backup                           │
 └────────────────────────────────────────────────────────────────────┘
```

**Where it runs.** Use an operator-managed server with persistent local storage. Keep development and production separate. The repo includes a `Dockerfile` and `docker-compose.yml`; configure the data path and backups locally. SQLite and Chroma need a filesystem with reliable locks.

**One server, not one adapter per provider.** All clients speak the same MCP. The only per-provider differences are auth and how much of the tool list to show, and both belong in the server's per-token config, not in separate adapters. The one exception is OAuth, which may sit in a front proxy (see option A3).

**Server cost.** One hub process uses about 250 MB RAM, and Chroma's default embedding model runs locally. Today's setup starts a 244 MB MCP server per agent session. With a hub, stdio-only sessions still start a small forwarder (`runtime.py:38`), and its memory use hasn't been measured. HTTP clients need no local process. There are no pay-per-token costs. Keep MemPalace's LLM-assisted features off, or point them at a local Ollama.

### Scoping

The palace already has wings, rooms and drawers. Use wings as the scope unit:

| Wing | What goes there | Who reads | Who writes |
|---|---|---|---|
| `shared` | Durable facts about Avery, his preferences, decisions | Everyone | Tailnet agents. Cloud writes are promoted from `inbox` |
| `project_<name>` | Per-repo decisions, quirks, branches | Agents working on that project + coordinator | Hooks and agents on that project |
| `agent_<id>` | Each agent's diary and scratch notes | That agent, plus coordinator | That agent only |
| `inbox` | Anything a cloud client writes | Everyone, shown as "unreviewed" | Cloud clients |
| `private` | Health and other sensitive info | Tailnet clients Avery picks | Tailnet clients Avery picks |

Identity follows the fork's RFC 005: `host:agent:project` (e.g. `laptop:claude:example-app`, `grok:coordinator:-`). The server stamps it from the token on every write, whatever the client claims.

### How writes get in

- **Tailnet coding agents: hooks.** Claude Code Stop/SessionEnd/PreCompact hooks already file transcripts and diary checkpoints. They must be pointed at the hub instead of a local palace (hook write routing exists for a local daemon, see `docs/hook-write-routing.md`; a remote hub URL plus token is new work, **verify**). Codex hooks run, but ingest is broken for Codex 0.163 rollouts. The ingest implementation needs to support the current rollout format.
- **Cloud clients: explicit tool calls only.** Grok Bot, claude.ai and ChatGPT have no hooks. They call `add_drawer`, `diary_write` or `kg_add` when told to remember something, or by a rule in each bot's system prompt ("file decisions and facts about Avery; never file chit-chat").
- **Noise control:**
  - Hooks write only to project and agent wings, never to `shared`.
  - The server rejects writes outside a token's allowed wings.
  - `check_duplicate` runs before every add.
  - For knowledge-graph facts, use `kg_supersede` instead of piling up conflicting entries.
- **No write conflicts.** One process owns the palace and already serializes writes (readers-writer lock in `http.py`). Drawers are verbatim and append-only, so two agents writing the same fact just makes a duplicate, and the dedupe check catches it.

### Choice A: how cloud clients reach the hub

| | A1. Tailscale Funnel + token **(start here)** | A2. Cloudflare Tunnel + Access | A3. Funnel + self-hosted OAuth gateway |
|---|---|---|---|
| Setup | `tailscale funnel` on the server, one ACL attribute | Domain on Cloudflare, `cloudflared` container, Access policy | A1 plus an OAuth gateway container (e.g. Pomerium, which advertises MCP support **(verify)**) |
| Who sees plaintext | Only the server. TLS ends on the box | Cloudflare. TLS ends at their edge | Only the server |
| Works for Grok Bot | Yes (header token) | Yes | Yes |
| Works for claude.ai / ChatGPT | No, they need OAuth | Yes, if Access's OAuth for MCP fits **(verify)** | Yes |
| Extras | No WAF or rate limiting. Provider-assigned hostname, ports 443/8443/10000 only | WAF, rate limits, custom domain | Self-hosted, most moving parts |

Recommendation: A1 now, for Grok Bot. Add A3 when Avery wants claude.ai and ChatGPT in. A2 is the fastest way to get OAuth, but it puts a third party in the middle of every memory, health-adjacent ones included.

Tailnet clients always go through `tailscale serve` (tailnet only). Because that is a proxy, the hub requires a token there too. That's fine, and it's how the hub tells clients apart.

### Choice B: keeping sensitive data away from cloud clients

| | B1. Two hubs **(start here)** | B2. One hub, per-token scopes |
|---|---|---|
| How | `private` palace on a tailnet-only hub, `shared` palace on the cloud-reachable hub. Tailnet agents mount both | One palace. A token config maps each token to allowed wings, allowed tools, and a default write wing |
| Code needed | None | Token config + filtering in search, get, list, KG and graph tools. A slim tool profile for cloud clients |
| Risk | A health fact filed in the wrong palace leaks | One missed filter path leaks everything. Needs tests on every read tool |
| Recall | Split: a query searches one palace at a time | One search over everything a client may see |

Recommendation: B1 to prove it out, because the health-data guarantee comes from the network, not from code. Build B2 as a fork feature once the hub sticks; even with B1, the shared hub needs B2's tool allowlist (see Risks).

## 3. Risks

**Privacy (health info).**
- Anything a cloud client recalls goes to Anthropic, OpenAI or xAI as part of that chat. That is a deliberate choice under the project's BYOK rule, but it means health data must not be retrievable by cloud tokens at all. B1 enforces that by network; B2 would do it in code.
- Other leaks to close:
  - Cloudflare TLS termination (A2).
  - Hub logs: check they record client metadata only, never drawer text.
  - At-rest encryption: use encrypted persistent storage.

**Prompt injection through memory.**
- A bot that reads a web page or email can file attacker text. Later, Claude Code (which has a shell) recalls it as "memory" and acts on it. With multiple bots writing, that's a real path. Mitigations:
  - Cloud writes land in `inbox`.
  - Coding agents search `shared` and project wings by default, and see `inbox` only when they ask for it.
  - Recall results show source, author and date, so text reads as quoted data rather than instructions.
- Separately, the shared hub must hide `mempalace_mine`, `sync`, `delete_*`, `update_drawer`, `hook_settings` and `reconnect` from cloud tokens. `mine` reads arbitrary paths on the server. `--read-only` hides these today but also blocks the writes Grok Bot needs, so a per-token tool allowlist is the first code change.

**Stale or wrong memories spreading.**
- One wrong fact gets recalled by every agent. Mitigations:
  - Show dates on every result.
  - Use the KG's `valid_from`/`valid_to` and `kg_supersede` for facts that change.
  - Run a weekly review pass (a local agent lists new `inbox` and `shared` items for Avery to keep, promote or invalidate).
  - Every recall skill says "if memory and the code disagree, trust the code". The trial showed agents treating an empty or wrong palace as the truth.
- Drawers stay verbatim and nothing is deleted. Invalidation marks a memory, it doesn't remove it.

**Cost and latency.**
- No per-token cost. The hub costs about 250 MB RAM plus disk; the trial palace was 1.1 MB for 46 drawers, and the embedding model is 167 MB.
- Search took 0.25–0.45 s locally in the trial. Tailscale adds a few to tens of ms; Funnel or a tunnel adds roughly 50–200 ms.
- Hooks must keep backgrounding their writes (they do today) to stay under the 500 ms hook budget.
- The 47 tool schemas cost about 9k tokens per client that loads them all. A slim cloud profile (search, get_drawer, add_drawer, diary_write, kg_query) saves context for the Grok bots.

**Availability.**
- If the server is down, every agent loses memory at once. RFC 004 (replicated palace) is the long-term fix.
- Short term: agents must treat "hub unreachable" as "no memory", not as an error that blocks work.

## 4. First step to prove it

Goal: a fact Grok Bot's coordinator files is recalled by Claude Code in a fresh session on the VM, and the reverse, with no health data anywhere in the test palace.

1. **Hub on the server.** Build the fork's Docker image and run `mempalace serve --host 127.0.0.1 --port 8765` with a fresh palace at `/path/to/data/mempalace-shared`. Note the auto-generated token. Set `MEMPALACE_MCP_EXTRA_ALLOWED_HOSTS=yourbox.your-tailnet.ts.net`, otherwise the loopback Host pin rejects requests that come through `tailscale serve`/`funnel`. Mount only the test palace volume into the container, so `mempalace_mine` can't read anything else on the server. The palace is throwaway, so the delete tools don't matter yet.
2. **Tailnet access.** `tailscale serve` HTTPS on a spare port to `127.0.0.1:8765`.
   - Add the hub to Claude Code on the VM (`claude mcp add --transport http …/mcp --header "Authorization: Bearer …"`).
   - Add it to one Codex session (`url` + `bearer_token_env_var` in a per-run `-c` override, not the global config).
3. **Cloud access.** Enable Funnel for the hub in the tailnet ACL (`funnel` node attribute) and run `tailscale funnel` on a second port to the same hub. Add it to the coordinator as a connector using the same token, since per-token scopes don't exist yet.
4. **Test.**
   - The coordinator files a fact; Claude Code and Codex each recall it in a fresh session.
   - Claude Code files a decision; the coordinator recalls it.
   - Record per-call latency from each client, hub RSS, and whether Grok Bot accepts the JSON-only streamable HTTP responses.
5. **Write up** the results next to this file. Tear down the Funnel afterwards unless Avery decides to keep it.

What it takes:
- About half a day.
- Funnel turned on in the tailnet policy.
- A Grok Bot connector slot.
- No new code for the test itself.

Code needed before real use, in order:

1. Per-token tool allowlist and wing scopes (B2-lite: hide `mine`/delete tools, force cloud writes into `inbox`).
2. Server-stamped `added_by` from the token. This depends on step 1: identity can only come from the token once each client has its own token.
3. Hooks that write to a remote hub URL with a token.
4. The Codex ingest fix (already in flight).
5. OAuth (A3) for claude.ai and ChatGPT.
