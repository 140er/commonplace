"""Project model choices (implementation follows S0)."""

from commonplace.projects.types import ModelChoice


def get(db, workspace_id: str, board: str) -> dict[str, ModelChoice | None]:
    raise NotImplementedError


def put(db, principal, board: str, body: dict) -> dict[str, ModelChoice | None]:
    raise NotImplementedError


def resolve(db, workspace_id: str, board: str, role: str) -> ModelChoice | None:
    raise NotImplementedError
