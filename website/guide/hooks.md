# Auto-Save Hooks

Two hooks for Claude Code and Codex that automatically save memories during work. No manual "save" commands needed.

## What They Do

| Hook | When It Fires | What Happens |
|------|--------------|-------------|
| **Save Hook** | Every 15 human messages | Blocks the AI, tells it to save key topics/decisions/quotes to the palace |
| **PreCompact Hook** | Right before context compaction | Emergency save — forces the AI to save everything before losing context |

The AI does the actual filing — it knows the conversation context, so it classifies memories into the right wings/halls/closets. The hooks just tell it **when** to save.

## Install — Claude Code

Add to `.claude/settings.local.json`:

```json
{
  "hooks": {
    "Stop": [{
      "matcher": "*",
      "hooks": [{
        "type": "command",
        "command": "/absolute/path/to/hooks/mempal_save_hook.sh",
        "timeout": 30
      }]
    }],
    "PreCompact": [{
      "hooks": [{
        "type": "command",
        "command": "/absolute/path/to/hooks/mempal_precompact_hook.sh",
        "timeout": 30
      }]
    }]
  }
}
```

Make them executable:
```bash
chmod +x hooks/mempal_save_hook.sh hooks/mempal_precompact_hook.sh
```

<a id="codex-cli"></a>

## Install — Codex CLI

For Codex CLI 0.163, add this to the project's `.codex/hooks.json`.
Use the nested `hooks` object and handler groups; a flat event-to-command
map does not work. `mempalace` must be on Codex's PATH (or replace it with
an absolute path to the executable).

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "mempalace hook run --harness codex --hook session-start",
        "timeout": 30
      }]
    }],
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "mempalace hook run --harness codex --hook stop",
        "timeout": 30
      }]
    }],
    "SessionEnd": [{
      "hooks": [{
        "type": "command",
        "command": "mempalace hook run --harness codex --hook session-end",
        "timeout": 3
      }]
    }],
    "PreCompact": [{
      "hooks": [{
        "type": "command",
        "command": "mempalace hook run --harness codex --hook precompact",
        "timeout": 90
      }]
    }]
  }
}
```

SessionEnd has a three-second Codex deadline; MemPalace backgrounds its save.
For inline `.codex/config.toml`, use the same nested structure:

```toml
[[hooks.Stop]]
[[hooks.Stop.hooks]]
type = "command"
command = "mempalace hook run --harness codex --hook stop"
timeout = 30
```

Choose JSON or TOML for each config layer so the hook is not registered twice.

### Project trust and hook review

Both gates must pass before project hooks run:

1. Codex ignores project `.codex/hooks.json` and `.codex/config.toml` unless
   the project is trusted in the global `~/.codex/config.toml` (or
   `$CODEX_HOME/config.toml`). Use Codex's project-trust flow to make this
   choice yourself. A trusted project has an entry such as
   `[projects."/absolute/path/to/project"]` with `trust_level = "trusted"`.
   MemPalace never writes Codex or Claude global configuration.
2. In the Codex TUI, open `/hooks` and review and trust each hook definition.
   Installing a plugin or trusting the project does not approve its hooks.
   Changed hooks need review again. Codex CLI 0.163 can silently skip
   unreviewed hooks, leaving an apparently working integration with no saves.

Run `mempalace status` from the project to check for an untrusted project
hook configuration. This is a read-only check of the global project-trust
entries, not a check that individual hook definitions have been reviewed.

For automation that has already vetted the commands, the per-invocation
`--dangerously-bypass-hook-trust` flag bypasses hook-definition review; it
is not a replacement for trusting a project config layer. You can instead
supply hooks explicitly for one invocation without writing global config:

```bash
codex exec --dangerously-bypass-hook-trust \
  -c 'hooks.SessionStart=[{hooks=[{type="command",command="mempalace hook run --harness codex --hook session-start",timeout=30}]}]' \
  -c 'hooks.Stop=[{hooks=[{type="command",command="mempalace hook run --harness codex --hook stop",timeout=30}]}]' \
  -c 'hooks.SessionEnd=[{hooks=[{type="command",command="mempalace hook run --harness codex --hook session-end",timeout=3}]}]' \
  -c 'hooks.PreCompact=[{hooks=[{type="command",command="mempalace hook run --harness codex --hook precompact",timeout=90}]}]' \
  'Your task'
```

See the [official Codex hooks documentation](https://learn.chatgpt.com/docs/hooks)
for configuration, project trust, and hook review.

## Configuration

Edit `mempal_save_hook.sh` to change:

- **`SAVE_INTERVAL=15`** — How many messages between saves. Lower = more frequent, higher = less interruption.
- **`STATE_DIR`** — Where hook state is stored (defaults to `~/.mempalace/hook_state/`)
- **`MEMPAL_DIR`** — Optional. Set to a conversations directory to auto-run `mempalace mine` on each save trigger.

## How It Works

### Save Hook (Stop event)

```
User sends message → AI responds → Stop hook fires
                                          ↓
                                  Count human messages in transcript
                                          ↓
                            ┌── < 15 since last save → let AI stop
                            │
                            └── ≥ 15 since last save → block + save
                                                            ↓
                                                    AI saves to palace
                                                            ↓
                                                    AI stops (flag set)
```

The `stop_hook_active` flag prevents infinite loops.

### PreCompact Hook

```
Context window full → PreCompact fires → ALWAYS blocks → AI saves → Compaction proceeds
```

No counting needed — compaction always warrants a save.

## Debugging

```bash
cat ~/.mempalace/hook_state/hook.log
```

Example output:
```
[14:30:15] Session abc123: 12 exchanges, 12 since last save
[14:35:22] Session abc123: 15 exchanges, 15 since last save
[14:35:22] TRIGGERING SAVE at exchange 15
[14:40:01] Session abc123: 18 exchanges, 3 since last save
```

## Cost

**Zero extra tokens.** The hooks are bash scripts that run locally. They don't call any API. The only "cost" is a few seconds of the AI organizing memories at each checkpoint.
