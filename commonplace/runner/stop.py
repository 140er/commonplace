"""Terminal evidence required by the version-one external run-stop contract."""

import re
from datetime import datetime


def terminal_evidence(accepted_at, ended_at):
    """Host UTC instants must prove termination strictly after stop acceptance."""
    values = (accepted_at, ended_at)
    if any(
        not isinstance(value, str)
        or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value)
        for value in values
    ):
        return False
    try:
        accepted, ended = (datetime.fromisoformat(value.replace("Z", "+00:00")) for value in values)
        return ended > accepted
    except ValueError:
        return False
