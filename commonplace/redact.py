"""Shared storage and display redactor interface."""

MAX_CHARS = 16_000
KINDS = (
    "private_key",
    "cmmn_oauth",
    "cmmn_token",
    "session_cookie",
    "login_link",
    "push_topic",
    "aws_key",
    "github_token",
    "gitlab_token",
    "anthropic_key",
    "openai_key",
    "slack_token",
    "stripe_key",
    "google_api_key",
    "npm_token",
    "jwt",
    "bearer",
    "basic_auth",
    "auth_header",
    "cookie_header",
    "url_credentials",
    "query_secret",
    "json_secret",
    "assignment",
    "cli_flag",
    "random_token",
    "json_secret_key",
    "too_long",
)


def redact(text) -> tuple[str, list[str]]:
    raise NotImplementedError


def redact_value(obj) -> tuple[object, list[str]]:
    raise NotImplementedError
