"""Machine-only chat relay routes under /api/runner/chat/: they carry redacted chat content to the
poster's own runner and answer text back. The card-runner actions in runner_api.py carry no user content.
"""

PREFIX = "/api/runner/chat/"


def register(gateway) -> None:
    pass
