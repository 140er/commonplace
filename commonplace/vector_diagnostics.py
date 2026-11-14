"""Public vector health diagnostics, without palace-wide index counts."""

VECTOR_DISABLED_REASON = "Vector index unavailable; repair required."


def public_vector_diagnostics(payload):
    """Copy only diagnostic objects that need their reason replaced or removed."""
    if not isinstance(payload, dict):
        return payload
    if payload.get("vector_disabled") is True:
        if payload.get("vector_disabled_reason") == VECTOR_DISABLED_REASON:
            return payload
        return {**payload, "vector_disabled_reason": VECTOR_DISABLED_REASON}
    if "vector_disabled_reason" in payload:
        return {key: value for key, value in payload.items() if key != "vector_disabled_reason"}
    return payload
