"""Hosting plans are undecided; see PRODUCT-DESIGN.md §14, Choice P.

Every self-service sign-up calls this once after its user and personal workspace
exist. The free tier attaches here when decided; until then, nothing is recorded
or enforced.
"""


def assign_signup_plan(db, user_id: str, workspace_id: str, *, via: str) -> None:
    return
