"""Opt-in local chat client contracts; no client loop is installed yet."""

from .agent import _CHILD_KEYS, _ConfigError

CONFIG_KEYS = frozenset({"hub_url", "token_env", "chat"})
CHAT_KEYS = frozenset({"default_cli", "context", "relay_always", "clis"})
CLI_KEYS = frozenset({"argv", "default_model", "tools"})
POLL_S = 3
CLI_TIMEOUT_S = 60
CHAT_CHILD_KEYS = _CHILD_KEYS + ("XDG_CONFIG_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR")
SAFE_ARGV = {
    "claude": {
        "any_of": ("-p", "--print"),
        "all_of": ("--strict-mcp-config", "--no-session-persistence"),
        "empty_value": "--tools",
        "deny": (
            "--dangerously-skip-permissions",
            "--allow-dangerously-skip-permissions",
            "--allowedTools",
            "--allowed-tools",
            "--mcp-config",
            "--add-dir",
            "--settings",
            "--plugin-dir",
            "--agents",
            "--agent",
            "--permission-mode",
        ),
    }
}
EGRESS_LINE = "chat: answers run your configured CLI under your own login; each prompt (your message, the project name and up to 15 trusted card titles) goes to that CLI's provider"
EGRESS_FULL_LINE = "chat: context full: prompts also carry recent chat lines from everyone in the project and all visible card titles"
TOOLS_LINE = "chat: {label} runs with its tools on (tools: true); chat text can steer those tools, including reading local files and sending them to the CLI's provider"


def validate_chat(chat: dict) -> dict:
    raise _ConfigError("chat not supported yet")


def load(config_path) -> dict:
    raise _ConfigError("chat not supported yet")


def check_lines(config: dict) -> list[str]:
    return []


def run(config_path, once: bool) -> int:
    return 4


def _ask_cli(chat: dict, cli: str, model: str | None, system: str, prompt: str) -> tuple[str, str]:
    raise NotImplementedError
