# Local Claude Code setup

Requires Python 3.10+, [uv](https://docs.astral.sh/uv/) and Claude Code already installed.
From a checkout, install and create a local workspace:

```sh
uv sync --extra dev
.venv/bin/commonplace init --admin-email you@example.com
```

Start the gateway in another terminal from the same checkout:

```sh
.venv/bin/commonplace serve --host 127.0.0.1 --port 8765
```

Keep it running. Back in the first terminal, issue a token and register the
HTTP endpoint:

```sh
export COMMONPLACE_TOKEN="$(.venv/bin/commonplace token add claude-code:local \
  --workspace personal --client claude-code)"
claude mcp add --transport http commonplace http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer $COMMONPLACE_TOKEN"
```

The equivalent project `.mcp.json` is below. Replace the placeholder with the
token printed once by `token add`. Keep that credential out of version control.

```json
{
  "mcpServers": {
    "commonplace": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp",
      "headers": {"Authorization": "Bearer <your-token>"}
    }
  }
}
```

## Repeatable offline check

On Linux or macOS, after `uv sync --extra dev` has prepared the dependency and
build cache, run:

```sh
python3 scripts/check_commonplace_setup.py
```

The check creates a fresh, non-editable installation from this checkout using
`uv sync --offline --frozen --no-dev --no-editable`. Missing cached dependencies
fail the check; it never falls back to downloading packages or Python.
It uses a temporary home, workspace, data directory and `.mcp.json`, with only
test-generated credentials and an `example.invalid` email. Existing environment
credentials and application settings are excluded.

It runs the installed `commonplace init`, `token add --client claude-code` and
`serve` commands, starts the real memory worker, and uses the temporary MCP
configuration to send `initialize`, `notifications/initialized` and `tools/list`
over loopback HTTP. Both memory and board tools must appear; missing and invalid
tokens must receive HTTP 401. No Claude process or account is needed. Python
socket access outside loopback is blocked inside the disposable environment;
no embedding model or external provider is used. The gateway, workers and
temporary files are removed on success or failure.

The check exits nonzero if setup exceeds 300 seconds. Timing starts before the
fresh installation and ends after authenticated tool discovery; queueing and
cleanup are excluded. A local Linux run on 2026-10-31 measured **1.11 seconds to
install, 3.38 seconds to connect**, exposing 33 tools. This measures an empty
client environment with a warm package cache. Installing Python, uv or Claude
Code, cold dependency downloads, interactive client approval and account sign-in
are outside this measurement; it does not establish their end-to-end time.

## Keep rules in your instructions file

The optional SessionStart script runs with Python 3.9+ and the standard library
on Linux and macOS. It needs a hub supporting `rules_get effective=true` and a
connection with `memory:read`. Install it explicitly from a checkout:

```sh
sh commonplace/agent-skill/install.sh --rules --dry-run
sh commonplace/agent-skill/install.sh --rules
```

This copies the script and skill; it does not edit client configuration. Keep
all destinations outside Git work trees, including paths reached by symlinks.
The rules files, identity binding, state and lock are private (mode 0600).

Use separate tokens, destinations, and client configurations for each role,
for example `conn:builder` and `conn:reviewer`. Each hook must use the same
token as that client configuration's Commonplace MCP connection. A reviewer
must not load a builder's instructions file. Give isolated client homes their
own settings, imports or AGENTS.md and MCP configuration. The token may come
from `COMMONPLACE_TOKEN`, or from `--token-file` pointing to a mode-0600 file;
never put a token in a command argument. The following examples assume those
separate role credentials already exist; they do not create tokens.

For a Claude Code builder configuration, with its matching MCP token in the
environment, bind the file once:

```sh
export COMMONPLACE_URL="https://hub.example"
python3 ~/.commonplace/bin/rules_sync.py sync --install \
  --out ~/.commonplace/rules/claude-builder.md
```

Add this import to that configuration's CLAUDE.md:

```text
@~/.commonplace/rules/claude-builder.md
```

Add the hook to that configuration's `settings.json` (replace `/path/to` with
absolute paths, preserving shell quoting when paths contain spaces):

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "python3 /path/to/rules_sync.py sync --out /path/to/claude-builder.md"
      }]
    }]
  }
}
```

For a Codex builder configuration, explicitly install a managed block near the
top of that configuration's global AGENTS.md. The script preserves all bytes
outside existing markers and keeps the target's mode and owner:

```sh
python3 ~/.commonplace/bin/rules_sync.py sync --install-block \
  --out ~/.commonplace/rules/codex-builder.md \
  --block /path/to/builder-config/AGENTS.md --file-cap 40960
```

Add the following to that configuration's `hooks.json`, and approve the hook
through the client's hook trust flow where required:

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "python3 /path/to/rules_sync.py sync --out /path/to/codex-builder.md --block /path/to/builder-config/AGENTS.md --file-cap 40960"
      }]
    }]
  }
}
```

Repeat setup with the reviewer token and separate `claude-reviewer.md` or
`codex-reviewer.md` destinations in a separate reviewer client configuration.
Switching a destination's token requires another explicit `--install` with
the same destination options. `--install-block` can also create markers; an
ordinary hook only restores missing markers when its committed file and state
prove that it previously published that block. Installation waits for any
active refresh and replaces the old rules with a pending stub before binding
the new identity. Manual installation errors exit nonzero.

Every session makes at most one request, with a 500 ms overall budget. Runs
that waited for the same destination may reuse the result just published.
The script refuses redirects, environment proxies, and non-loopback plain
HTTP. A failed refresh preserves the last copy and reports that it could not
be refreshed; a hook exits zero even on invalid arguments. A different token
cannot overwrite the bound copy and produces an identity warning.

The Claude file is limited to 16,000 UTF-8 bytes and the Codex managed block
to 6,000, including framing. `--max-bytes` can lower these budgets. Only whole
rules are included. Incomplete copies show a count of rules not shown and
point to `rules_get`; they never carry a usable rules sequence. The server's
effective reply cap remains 24,000 bytes. Mode `off` is a complete copy without
a rules sequence; unavailable or lost-store replies preserve the last copy.
A whole-file cap refuses an update that would exceed it, preserving existing
instructions. Keep Codex's default 40 KiB `project_doc_max_bytes`; setup does
not reduce it. Reuse the same destination and budget options for every hook.

A successful hook emits SessionStart `additionalContext` with a content
version and the file path, telling the model to read it if its loaded copy has
another version. This line never contains the rules sequence. Local Claude
Code project-import and Codex SessionStart probes delivered synthetic
canaries; the Codex hook probe required a per-process trust bypass. MCP
initialize instructions alone were not honored in the measured Codex CLI
sessions. Hooks, trust, environment inheritance and user-level imports for
T3-launched sessions remain unmeasured. Verify each launch route before
depending on it; a route without SessionStart hooks needs an explicit start
step. These probes do not establish support for every client version or route.

To remove this integration, remove its hook entry, Claude import or Codex
markers, then optionally delete the private destination and its sidecars.
