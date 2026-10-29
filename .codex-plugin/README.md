# MemPalace - Codex CLI Plugin

Give your AI a persistent memory -- mine projects and conversations into a searchable palace backed by ChromaDB, with 47 MCP tools, auto-save hooks, and guided skills.

## Prerequisites

- Python 3.10+
- Codex CLI installed and configured
- `uv tool install mempalace` (recommended) or `pip install mempalace`

## Installation

1. Add the repo to the Codex marketplaces:

```bash
codex plugin marketplace add MemPalace/mempalace
```

2. Install the plugin:

```bash
codex plugin add mempalace@mempalace
```

3. Initialize your palace in the Codex TUI:

```bash
codex
> $mempalace:mempalace init
```

## Available Skills

| Skill | Description |
|-------|-------------|
| `$mempalace:mempalace` | Install, configure, and operate MemPalace, including a private local palace, a shared-brain hub, or a client joining an existing hub |
| `$mempalace:mempalace-recall` | Recall protocol for MemPalace — search the palace before answering about past work, people, projects, or prior decisions |
| `$mempalace:mempalace-task` | Create, hand off, claim, execute, and close agent tasks through the MemPalace logstream |

### Skill Commands

The main `$mempalace:mempalace` skill can be invoked with six different subcommands. `$mempalace <command>` can be used as a short form invocation.

| Command | Description |
|---------| ------------|
| `$mempalace help` | Show available commands and usage tips |
| `$mempalace init` | Initialize a new memory palace |
| `$mempalace search` | Semantic search across all mined memories |
| `$mempalace mine` | Mine a project or conversation into your palace |
| `$mempalace status` | Show palace status, room counts, and health |
| `$mempalace audit` | Score palace organization, then a guided repair session |

## Hooks

For Codex CLI 0.163, configure `SessionStart`, `Stop`, `SessionEnd`, and
`PreCompact` with the [manual hook setup](../website/guide/hooks.md#install--codex-cli).
The plugin manifest registers MCP and skills; it does not register the
`.codex-plugin/hooks.json` reference file. That file already uses the nested
`{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "..."}]}]}}`
format; flat command entries do not work.

Project `.codex/hooks.json` and `.codex/config.toml` are ignored unless the
project is trusted in global `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`).
Use Codex's project-trust flow; MemPalace never changes global Codex or Claude
configuration. Then open `/hooks` in the Codex TUI to review and trust each
hook definition. Project trust and plugin installation do not approve hooks;
new or changed definitions require review. Codex CLI 0.163 can silently skip
unreviewed hooks, so verify these steps even if MCP tools work.

`mempalace status` checks project-trust entries read-only when project hooks
are present. It cannot verify individual hook reviews. For automation with
commands you have already vetted, `--dangerously-bypass-hook-trust` bypasses
hook review for that invocation; project-layer trust is still required.
The manual setup also shows per-invocation `-c 'hooks.Stop=[{hooks=[...]}]'`
overrides that need no global config writes.

See the [official Codex hooks documentation](https://learn.chatgpt.com/docs/hooks).

Set the `MEMPAL_DIR` environment variable to a directory path to automatically run `mempalace mine` on that directory during each save trigger.

## Support

- Repository: https://github.com/MemPalace/mempalace
- Issues: https://github.com/MemPalace/mempalace/issues
