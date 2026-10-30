#!/bin/bash
# Client-side Memory handoff. The Python hook always falls back on failure.
export PYTHONSAFEPATH=1
exec "${COMMONPLACE_PYTHON:-python3}" -m commonplace.handoff.hook "$@"
