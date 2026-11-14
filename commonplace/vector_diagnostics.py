"""Public vector health diagnostics, without palace-wide index counts."""

VECTOR_DISABLED_REASON = "Vector index unavailable; repair required."


def sees_raw_vector_reason(principal):
    """Only a workspace owner reading every wing may see the raw index reason.

    Members and tokens scoped below the whole workspace stay on the fixed reason.
    """
    policy = getattr(principal, "policy", None)
    return getattr(principal, "role", None) == "owner" and bool(
        getattr(policy, "sees_all_wings", False)
    )


def public_vector_diagnostics(payload, raw_reason=False):
    """Copy only diagnostic objects that need their reason replaced or removed.

    ``raw_reason`` keeps the worker's own non-empty reason when the disabled flag
    is true (the fixed reason fills a missing one); a reason without that flag is
    still omitted.
    """
    if not isinstance(payload, dict):
        return payload
    if payload.get("vector_disabled") is True:
        reason = payload.get("vector_disabled_reason")
        if reason == VECTOR_DISABLED_REASON or (raw_reason and isinstance(reason, str) and reason):
            return payload
        return {**payload, "vector_disabled_reason": VECTOR_DISABLED_REASON}
    if "vector_disabled_reason" in payload:
        return {key: value for key, value in payload.items() if key != "vector_disabled_reason"}
    return payload
