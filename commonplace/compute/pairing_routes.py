"""``/api/compute/pairings`` plus the two routes the machine itself calls.

Owner routes: ``routes.handle`` has already authenticated the person, decided owner or member
for the method (members read, only owners write), refused query strings and parsed the path;
the owner check ran before any body was read. Every refusal names a field (when it is one) and
a fixed reason.

Machine routes: ``POST /api/runner/pairing-status`` takes the machine's own credential;
``POST /api/runner-pairing/check`` takes no credential at all, only the pairing id and the
one-time code, and answers with a reason when the code cannot work.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

from commonplace.control_db import ControlError
from commonplace.http_util import log_failure, send_json, send_text
from commonplace.runner import enrollment, enrollment_api

from . import pairing
from .control import StoreError

LOG = logging.getLogger(__name__)
STATUS = "/api/runner/pairing-status"
CHECK = "/api/runner-pairing/check"
LAUNCHER = "/machine-setup.sh"
END_MARK = "COMMONPLACE_SETUP_END"
MAX_BYTES = 2048
_OPTIONAL = pairing.CREATE_FIELDS - pairing.CREATE_REQUIRED
_CONTROL_STATUS = {"cancelled": 200, "expired": 200}


def dispatch(h, store, principal, parts, auth, body_reader):
    """``parts`` is the path after ``/api/compute/pairings``."""
    gateway, db = store.gateway, store.gateway.db
    pairing.ensure(db)
    # ``routes.handle`` has just re-read this session under the control lock (``_owner`` for a write, ``_member`` for a
    # read), so the role on the principal is current; reading it again here would use the shared connection unlocked.
    owner = principal.role == "owner"
    if h.command == "GET":
        if not parts:
            return send_json(h, 200, pairing.list_pairings(db, principal, owner=owner))
        if parts == ["options"]:
            return send_json(h, 200, pairing.options(db, gateway, principal, owner=owner))
        if len(parts) == 1:
            return send_json(h, 200, pairing.get(db, principal, parts[0], owner=owner))
    elif h.command == "POST":
        if not parts:
            body = body_reader(h, pairing.CREATE_FIELDS, _OPTIONAL)
            _charge(db, principal.workspace_id, "pairing_create", pairing.CREATE_PER_MINUTE)
            created, item, setup = pairing.create(db, gateway, principal, body)
            payload = {"pairing": item, "repeated": not created}
            if setup:
                payload["setup"] = setup
            return send_json(h, 201 if created else 200, payload)
        if len(parts) == 2 and parts[1] in ("accept", "close"):
            with db._lock:
                pairing._find(db, principal.workspace_id, parts[0])
            fields = pairing.ACCEPT_FIELDS if parts[1] == "accept" else pairing.CLOSE_FIELDS
            body = body_reader(h, fields, ())
            action = pairing.accept if parts[1] == "accept" else pairing.close
            return send_json(h, 200, {"pairing": action(db, principal, parts[0], body)})
    raise StoreError("not_found", 404, reason="route")


def _charge(db, workspace, purpose, limit):
    """Durable per-workspace counter; a workspace with setup off has none, so it is not limited."""
    try:
        enrollment.charge(db, workspace, purpose, limit)
    except enrollment.Refused as exc:
        if exc.status == 429:
            raise pairing.refuse(None, "rate_limited", "rate_limited", 429) from None


# ---- machine routes ----------------------------------------------------------------------------


def _read(h):
    """The JSON body, or None after the refusal has been sent."""
    return enrollment_api._body(h)


def _refusal(h, exc):
    h.close_connection = True
    detail = {k: v for k, v in (("field", exc.field), ("reason", exc.reason)) if v}
    send_json(h, exc.status, {"error": exc.code, **detail})


def _status(gateway, h):
    """A machine's own step report, or heartbeat."""
    path = urlsplit(h.path).path
    principal = gateway.principal_for(h)
    if enrollment_api.preflight(gateway, h, path, principal):
        return
    if principal is None or getattr(principal, "runner", None) != "machine":
        h.close_connection = True
        send_json(h, 404, {"error": "not found"})
        return
    data = _read(h)
    if data is None:
        return
    try:
        enrollment.charge(
            gateway.db, principal.workspace_id, "pairing_report", pairing.REPORT_PER_MINUTE
        )
        result = pairing.report(gateway.db, principal, data)
    except enrollment.Refused as exc:
        send_json(h, exc.status, {"error": "not found" if exc.status == 404 else "rate limited"})
        return
    except StoreError as exc:
        _refusal(h, exc)
        return
    except (ValueError, ControlError):
        send_json(h, 400, {"error": "invalid_request", "reason": "body"})
        return
    except Exception as exc:
        log_failure(LOG, "Pairing report unavailable", exc)
        send_json(h, 503, {"error": "pairing unavailable"})
        return
    send_json(h, 200, result)


def _check(gateway, h):
    """Is this code usable, and if not, why. No credential: the code is the authority."""
    refused = (
        gateway.principal_for(h) is not None
        or h.headers.get("Authorization")
        or h.headers.get("Cookie")
    )
    if refused:
        h.close_connection = True
        send_json(h, 404, {"error": "not found"})
        return
    data = _read(h)
    if data is None:
        return
    workspace = data.get("workspace_id")
    try:
        if not isinstance(workspace, str) or len(workspace) > 80:
            raise ValueError("invalid check")
        try:
            enrollment.charge(gateway.db, workspace, "pairing_check", pairing.CHECK_PER_MINUTE)
        except enrollment.Refused as exc:
            if exc.status == 429:
                limited = pairing.REASONS["rate_limited"]
                send_json(
                    h,
                    429,
                    {
                        "usable": False,
                        "reason": "rate_limited",
                        "text": limited[0],
                        "next": limited[1],
                    },
                )
                return
        result = pairing.check(gateway.db, data)
    except (ValueError, ControlError):
        send_json(h, 400, {"error": "invalid_request", "reason": "body"})
        return
    except Exception as exc:
        log_failure(LOG, "Pairing check unavailable", exc)
        send_json(h, 503, {"error": "pairing unavailable"})
        return
    send_json(h, 200, result)


_SH = (
    r"""#!/bin/sh
# Commonplace machine setup. Your Commonplace site serves this file; it holds no secret.
# The one-time code arrives on standard input. It is never an argument or an environment value.
set -u
ENVELOPE=${1:-}
CODE=
IFS= read -r CODE || true

say() { printf '%s\n' "$@"; }
ok_text() { case "$1" in "") return 1 ;; *[!A-Za-z0-9_-]*) return 1 ;; esac; }

decode() {
  s=$(printf '%s' "$ENVELOPE" | tr '_-' '/+')
  n=$(( (4 - ${#s} % 4) % 4 ))
  while [ "$n" -gt 0 ]; do s="$s="; n=$((n - 1)); done
  printf '%s' "$s" | base64 -d 2>/dev/null
}
field() { printf '%s' "$DECODED" | sed -n 's/.*"'"$1"'":"\([^"]*\)".*/\1/p'; }

# Tell the app why this machine cannot be set up, without spending the code. Best effort.
report() {
  command -v curl >/dev/null 2>&1 || return 0
  DECODED=$(decode) || return 0
  HUB=$(field hub); WS=$(field workspace_id); PID=$(field pairing_id)
  ok_text "$WS" && ok_text "$PID" && ok_text "$CODE" || return 0
  case "$HUB" in
    https://*) ;;
    http://127.0.0.1 | http://127.0.0.1:* | http://localhost | http://localhost:*) ;;
    *) return 0 ;;
  esac
  case "$HUB" in *[!A-Za-z0-9.:/_-]*) return 0 ;; esac
  printf '{"workspace_id":"%s","pairing_id":"%s","code":"%s","report":{"step":"prerequisites","state":"failed","cause":"%s"}}' \
    "$WS" "$PID" "$CODE" "$1" |
    curl -fsS -m 20 -H 'Content-Type: application/json' --data-binary @- \
      "$HUB/api/runner-pairing/check" >/dev/null 2>&1 || true
}

PY=$(command -v python3 2>/dev/null) || PY=
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  report missing_python
  say "Python 3.10 or later is required." \
    "Install it (sudo apt install python3), then open Cloud workspaces in the app and choose Set up again." \
    "The app shows this too. Nothing was changed on this machine."
  exit 1
fi

DIR=$(mktemp -d "${TMPDIR:-/tmp}/commonplace-setup.XXXXXX") || { say "Could not create a temporary folder."; exit 1; }
trap 'rm -rf "$DIR"' EXIT HUP INT TERM
cat > "$DIR/setup.py" <<'"""
    + END_MARK
    + """'
"""
)


def launcher_text():
    """The shell launcher with this package's stdlib-only setup program inside it."""
    from commonplace.runner import machine_setup

    source = Path(machine_setup.__file__).read_text(encoding="utf-8")
    if END_MARK in source or "\r" in source:
        raise RuntimeError("setup program cannot be embedded")
    tail = f'{END_MARK}\nprintf \'%s\\n\' "$CODE" | "$PY" -I "$DIR/setup.py" "$ENVELOPE"\nexit $?\n'
    return _SH + source + ("" if source.endswith("\n") else "\n") + tail


def _launcher(gateway, h):
    """The setup launcher: public, identical for every caller, nothing tenant-specific."""
    try:
        text = launcher_text()
    except Exception as exc:
        log_failure(LOG, "Machine setup launcher unavailable", exc)
        send_json(h, 503, {"error": "setup unavailable"})
        return
    send_text(h, 200, text, "text/x-shellscript; charset=utf-8")


def install(gateway):
    pairing.ensure(gateway.db)
    gateway.add_route("GET", LAUNCHER, lambda h: _launcher(gateway, h))
    gateway.add_route("POST", STATUS, lambda h: _status(gateway, h))
    gateway.add_route("POST", CHECK, lambda h: _check(gateway, h))
