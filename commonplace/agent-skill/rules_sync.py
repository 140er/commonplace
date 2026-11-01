#!/usr/bin/env python3
"""Explicitly installed, identity-bound SessionStart rules (Python 3.9+, stdlib).

No package installation is needed. POSIX flock serializes each destination;
unsupported locking fails open. A process watchdog bounds even DNS, a stalled
stdin producer, or a response that keeps sending bytes just before a timeout.
"""

# ruff: noqa: E402 -- Start the deadline before importing the rest of the stdlib.

import time

STARTED = time.monotonic()

import argparse
import hashlib
import ipaddress
import json
import os
import re
import select
import stat
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

HOOK_SECONDS = 0.45  # Leave room for interpreter startup and shutdown within 500 ms.
CLAUDE_BYTES = 16_000
CODEX_BYTES = 6_000
REPLY_BYTES = 24_000
END = b"<!-- commonplace-rules:end -->"
BEGIN = re.compile(rb"<!-- commonplace-rules:begin(?: ([0-9a-f]{12}))? -->")
HEADINGS = ("Hard limits", "Permissions", "Standing instructions", "Style")
STUB = (
    "Commonplace rules are not available for this connection yet. "
    "Call rules_get format=md effective=true.\n"
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def read_json(path):
    try:
        value = json.loads(path.read_bytes())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def remaining(deadline):
    left = deadline - time.monotonic() if deadline is not None else 60
    if left <= 0:
        raise TimeoutError("SessionStart deadline")
    return left


def safe_path(path):
    resolved = path.expanduser().resolve()
    if any((parent / ".git").exists() for parent in (resolved, *resolved.parents)):
        raise ValueError("Rules destinations must be outside Git work trees")
    return resolved


def paths_for(args):
    # Sidecars follow the supplied output name; check every realpath separately.
    out = Path(args.out).expanduser().absolute()
    paths = {"out": out, **{key: Path(str(out) + "." + key) for key in ("owner", "state", "lock")}}
    if args.block:
        paths["block"] = Path(args.block).expanduser().absolute()
    resolved = {key: safe_path(path) for key, path in paths.items()}
    if len(set(resolved.values())) != len(resolved):
        raise ValueError("Rules destinations must be distinct")
    existing = [p.stat() for p in resolved.values() if p.exists()]
    if len({(s.st_dev, s.st_ino) for s in existing}) != len(existing):
        raise ValueError("Rules destinations must be distinct")
    return resolved


def connection(args):
    url = os.environ.get("COMMONPLACE_URL", "https://cmmn.dev").rstrip("/")
    address = urlsplit(url)
    if (
        address.scheme not in ("https", "http")
        or not address.hostname
        or address.username is not None
        or address.password is not None
        or address.query
        or address.fragment
        or any(ord(c) <= 32 or ord(c) == 127 for c in url)
    ):
        raise ValueError("Use an HTTPS gateway URL or loopback HTTP")
    _ = address.port  # Validate the port before reading a credential.
    if address.scheme == "http":
        try:
            local = ipaddress.ip_address(address.hostname).is_loopback
        except ValueError:
            local = address.hostname.lower().rstrip(".") == "localhost"
        if not local:
            raise ValueError("Use an HTTPS gateway URL or loopback HTTP")
    if args.token_file:
        with Path(args.token_file).expanduser().open("rb") as stream:
            info = os.fstat(stream.fileno())
            if stat.S_IMODE(info.st_mode) != 0o600 or not stat.S_ISREG(info.st_mode):
                raise ValueError("Token file must be a regular file with mode 0600")
            raw = stream.read(16_385)
        if len(raw) > 16_384:
            raise ValueError("Invalid token file")
        token = raw.decode("utf-8").strip()
    else:
        token = os.environ.get("COMMONPLACE_TOKEN", "")
    if not token or not token.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in token):
        raise ValueError("Configure a token through the environment or a private token file")
    return url, token, digest(json_bytes([url, token]))


def binding(args, paths, identity):
    limit = CODEX_BYTES if args.block else CLAUDE_BYTES
    budget = args.max_bytes if args.max_bytes is not None else limit
    if not 600 <= budget <= limit or (args.file_cap is not None and args.file_cap < 600):
        raise ValueError("Invalid rules size budget")
    if args.file_cap is not None and not args.block:
        raise ValueError("A whole-file cap requires a managed block")
    return {
        "identity": identity,
        "out": str(paths["out"]),
        "block": str(paths["block"]) if args.block else None,
        "max_bytes": budget,
        "file_cap": args.file_cap,
    }


@contextmanager
def exclusive(path, deadline):
    import fcntl

    safe_path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
        waited = False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            waited = True
            # The CLI watchdog also bounds blocking flock. Manual installation
            # deliberately has no deadline and waits for the publisher to finish.
            fcntl.flock(fd, fcntl.LOCK_EX)
        remaining(deadline)
        yield waited
    finally:
        os.close(fd)


def atomic_write(path, data, check, deadline, expected=None):
    """Stage beside the destination, retaining block metadata and external edits."""
    remaining(deadline)
    safe_path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.stat() if expected is not None and path.exists() else None
    fd, temporary = tempfile.mkstemp(prefix=".commonplace-rules-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if metadata is not None:
                current = os.fstat(stream.fileno())
                if (current.st_uid, current.st_gid) != (metadata.st_uid, metadata.st_gid):
                    os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
                os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        remaining(deadline)
        safe_path(path)
        check()
        if expected is not None and (path.read_bytes() if path.exists() else b"") != expected:
            raise ValueError("Instructions changed during refresh; try next session")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def marker_parts(raw):
    starts = list(BEGIN.finditer(raw))
    if not starts and END not in raw and b"<!-- commonplace-rules:begin" not in raw:
        return None
    if len(starts) != 1 or raw.count(END) != 1:
        raise ValueError("Invalid managed block markers")
    start = starts[0]
    end = raw.index(END)
    if end < start.end():
        raise ValueError("Invalid managed block markers")
    return start, end + len(END)


def managed(data, identity):
    return b"<!-- commonplace-rules:begin " + identity[:12].encode() + b" -->\n" + data + END


def block_plan(path, data, owner, allowed, *, add=False):
    raw = path.read_bytes() if path.exists() else b""
    parts = marker_parts(raw)
    replacement = managed(data, owner["identity"])
    if parts is None:
        if not add:
            raise ValueError("Install managed block markers explicitly first")
        updated = replacement + b"\n\n" + raw
    else:
        begin, end = parts
        old_id = begin[1].decode() if begin[1] else None
        if old_id is not None and old_id not in allowed:
            raise ValueError("Managed block belongs to another connection")
        updated = raw[: begin.start()] + replacement + raw[end:]
    if owner["file_cap"] is not None and len(updated) > owner["file_cap"]:
        raise ValueError("Instructions would exceed the whole-file cap")
    return raw, updated, digest(replacement)


def owner_check(paths, expected):
    if read_json(paths["owner"]) != expected:
        raise ValueError("Rules binding changed")


def install(args, paths, owner):
    if args.install_block and not args.block:
        raise ValueError("--install-block requires --block")
    with exclusive(paths["lock"], None):
        before = read_json(paths["owner"])
        allowed = {owner["identity"][:12]}
        if before and before.get("out") == owner["out"] and before.get("block") == owner["block"]:
            allowed.add(before["identity"][:12])
        stub = (
            f"<!-- Commonplace rules pending. Identity {owner['identity'][:12]}. -->\n" + STUB
        ).encode()
        plan = (
            block_plan(paths["block"], stub, owner, allowed, add=args.install_block)
            if args.block
            else None
        )

        def check():
            owner_check(paths, before)

        # During a rebind, replace the artifact the client reads before the file.
        if plan:
            raw, updated, _ = plan
            atomic_write(paths["block"], updated, check, None, expected=raw)
        atomic_write(paths["out"], stub, check, None)
        atomic_write(paths["owner"], json_bytes(owner), check, None)
        safe_path(paths["state"])
        paths["state"].unlink(missing_ok=True)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, token, deadline):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "rules_get", "arguments": {"format": "md", "effective": True}},
    }
    request = Request(
        url + "/mcp",
        data=json_bytes(body),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    # No retries, initialize, redirect, environment proxy, or credential in argv.
    with build_opener(ProxyHandler({}), NoRedirect()).open(
        request, timeout=remaining(deadline)
    ) as response:
        raw = response.read(256_001)  # JSON can escape each UTF-8 reply byte sixfold.
    remaining(deadline)
    if len(raw) > 256_000:
        raise ValueError("Oversized response")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("error") or value.get("id") != 1:
        raise ValueError("Invalid rules response")
    result = value.get("result")
    if not isinstance(result, dict) or result.get("isError"):
        raise ValueError("Rules request refused")
    content = result.get("content")
    if not isinstance(content, list) or len(content) != 1 or content[0].get("type") != "text":
        raise ValueError("Invalid rules response")
    text = content[0].get("text")
    if not isinstance(text, str) or len(text.encode("utf-8")) > REPLY_BYTES:
        raise ValueError("Invalid rules response")
    return text


def parse_reply(text):
    lines = text.removesuffix("\n").split("\n")
    # Only this final line can grant a usable version. Never search rule text.
    marker = lines[-1]
    match = re.fullmatch(r"rules_seq ([1-9][0-9]{8}|off|too_large(?:: .*)?)", marker)
    if not match:
        raise ValueError("Rules unavailable; keep the last copy")
    incomplete = match[1].startswith("too_large")
    tail_lines = 3 if incomplete else 2
    count = re.fullmatch(r"effective rules: ([0-9]+)", lines[0])
    if (
        not count
        or len(lines) < tail_lines + 1
        or not re.fullmatch(r"seq [0-9]+", lines[-tail_lines])
    ):
        raise ValueError("Not an effective rules reply")
    body = lines[1:-tail_lines]
    total = int(count[1])
    if incomplete:
        omitted = re.fullmatch(r"Incomplete: ([0-9]+) rules not shown\.", lines[-2])
        if not omitted:
            raise ValueError("Missing incomplete marker")
        missing = int(omitted[1])
    else:
        missing = 0
    rows, heading = [], None
    # Keep each server-rendered rule verbatim; only whole quoted lines fit.
    quoted = r'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"'
    rule = re.compile(
        r"- (?:\[(?:board|repo|author) (?:[A-Za-z0-9._/:-]+|" + quoted + r")\] )?" + quoted
    )
    for line in body:
        if not line:
            continue
        if line in ("## " + title for title in HEADINGS):
            heading = line[3:]
        elif heading and rule.fullmatch(line) and not any(c in line for c in "<>&@"):
            rows.append((heading, line))
        else:
            raise ValueError("Invalid rules markdown")
    if len(rows) + missing != total:
        raise ValueError("Incomplete rules response")
    return "\n".join(body), rows, total, match[1] if match[1].isdigit() else None, incomplete


def render_file(text, owner):
    md, rows, total, seq, incomplete = parse_reply(text)
    version = digest(md.encode())[:8]
    identity = owner["identity"][:12]

    def render(selected, complete):
        title = f"version {version}" + (f", rules_seq {seq}" if complete and seq else "")
        header = (
            "<!-- Written by Commonplace rules_sync at session start. Do not edit: "
            f"it is overwritten. Identity {identity}. -->\n# Commonplace rules ({title})\n\n"
            "The workspace owner set or accepted these rules. Follow them while you work in this workspace.\n"
            "Each rule is one JSON string; the quotes are not part of the rule. "
        )
        if not complete:
            header += (
                "This list is incomplete. Call rules_get format=md effective=true for all of them "
                "and the rules_seq to pass on write tools (if it gives one).\n\n"
            )
        elif seq:
            header += f"Pass rules_seq={seq} on Commonplace write tools.\n\n"
        else:
            header += "Call rules_get format=md effective=true when you need the current set.\n\n"
        if complete:
            return (header + md).encode()
        groups = []
        for heading in HEADINGS:
            group = [line for kind, line in selected if kind == heading]
            if group:
                groups.append("## " + heading + "\n\n" + "\n".join(group) + "\n")
        counts = {
            heading: sum(kind == heading for kind, _ in rows)
            - sum(kind == heading for kind, _ in selected)
            for heading in HEADINGS
        }
        labels = [f"{n} {heading.lower()}" for heading, n in counts.items() if n]
        unknown = total - len(rows)
        if unknown:
            labels.append(f"{unknown} other rules")
        tail = "Not shown: " + (", ".join(labels) or "0 rules") + ".\n"
        return (header + "\n".join(groups) + "\n" + tail).encode()

    def fits(data):
        size = len(managed(data, owner["identity"])) if owner["block"] else len(data)
        return size <= owner["max_bytes"]

    data = render(rows, not incomplete)
    if fits(data):
        return data, md, version, seq if not incomplete else None
    shown = []
    for row in rows:
        if fits(render(shown + [row], False)):
            shown.append(row)
    data = render(shown, False)
    if not fits(data):
        raise ValueError("Budget cannot fit the rules header")
    return data, md, version, None


def artifacts_match(paths, state, owner):
    try:
        if not state or state.get("identity") != owner["identity"]:
            return False
        if digest(paths["out"].read_bytes()) != state.get("file_hash"):
            return False
        if owner["block"]:
            raw = paths["block"].read_bytes()
            parts = marker_parts(raw)
            if not parts or digest(raw[parts[0].start() : parts[1]]) != state.get("block_hash"):
                return False
        return bool(re.fullmatch("[0-9a-f]{8}", state.get("version", "")))
    except (OSError, ValueError):
        return False


def context_line(text):
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def success(owner, version):
    return context_line(
        f"Commonplace rules version {version} are in {owner['out']}. "
        "If the copy in your instructions shows another version, read that file before you act."
    )


def warning(args):
    if not args or not Path(args.out).expanduser().is_file():
        return None
    if not Path(str(Path(args.out).expanduser()) + ".owner").is_file():
        return None
    return context_line(
        f"Commonplace rules in {Path(args.out).expanduser().absolute()} could not be refreshed "
        "at this session start. Call rules_get format=md effective=true before you act."
    )


def sync(args, paths, owner, url, token, deadline):
    before = read_json(paths["owner"])
    if before is None:
        return None
    if before.get("identity") != owner["identity"]:
        return context_line(
            f"Commonplace rules in {owner['out']} belong to another connection. "
            "Call rules_get format=md effective=true before you act."
        )
    if before != owner:
        raise ValueError("Use the installed destination settings")

    def check():
        owner_check(paths, owner)

    observed_state = read_json(paths["state"])
    with exclusive(paths["lock"], deadline) as waited:
        check()
        state = read_json(paths["state"])
        if waited and state and state != observed_state and artifacts_match(paths, state, owner):
            return success(owner, state["version"])
        # Repair from the committed file before fetching, including a generator
        # that removed the entire block. Without that proof, missing markers are
        # never inserted by an ordinary hook.
        if owner["block"] and state and state.get("identity") == owner["identity"]:
            try:
                data = paths["out"].read_bytes()
            except FileNotFoundError:
                data = b""
            if data and digest(data) == state.get("file_hash"):
                raw, updated, _ = block_plan(
                    paths["block"], data, owner, {owner["identity"][:12]}, add=True
                )
                if raw != updated:
                    atomic_write(paths["block"], updated, check, deadline, expected=raw)
        data, md, version, seq = render_file(fetch(url, token, deadline), owner)
        plan = (
            block_plan(paths["block"], data, owner, {owner["identity"][:12]})
            if args.block
            else None
        )
        check()
        if not paths["out"].exists() or paths["out"].read_bytes() != data:
            atomic_write(paths["out"], data, check, deadline)
        block_hash = None
        if plan:
            raw, updated, block_hash = plan
            if updated != raw:
                atomic_write(paths["block"], updated, check, deadline, expected=raw)
            else:
                check()
                if paths["block"].read_bytes() != raw:
                    raise ValueError("Instructions changed during refresh")
        state = {
            "identity": owner["identity"],
            "rules_seq": seq,
            "md_hash": digest(md.encode()),
            "version": version,
            "file_hash": digest(data),
            "block_hash": block_hash,
            "refreshed_ns": time.monotonic_ns(),
        }
        atomic_write(paths["state"], json_bytes(state), check, deadline)
        return success(owner, version)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("Invalid arguments; see --help")


def parser():
    root = Parser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    command = commands.add_parser("sync", help="Refresh an explicitly installed rules destination")
    command.add_argument("--out", required=True, help="Private rules file outside Git work trees")
    command.add_argument("--block", help="Instructions file containing an installed managed block")
    command.add_argument(
        "--token-file", help="Token file with mode 0600 (otherwise COMMONPLACE_TOKEN)"
    )
    command.add_argument(
        "--max-bytes", type=int, help="Lower the 16000-byte file or 6000-byte block budget"
    )
    command.add_argument(
        "--file-cap", type=int, help="Refuse a block update exceeding this whole-file size"
    )
    setup = command.add_mutually_exclusive_group()
    setup.add_argument(
        "--install", action="store_true", help="Bind identity and replace rules with a stub"
    )
    setup.add_argument(
        "--install-block", action="store_true", help="Explicitly add markers and bind identity"
    )
    return root


def hook_input(deadline):
    # Input fields do not determine identity or paths. Ignore invalid/empty JSON;
    # do not wait for a producer that leaves its pipe open.
    remaining(deadline)
    try:
        if select.select([sys.stdin], [], [], 0)[0]:
            json.loads(os.read(sys.stdin.fileno(), 65_536))
    except (OSError, ValueError, TypeError):
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    manual = any(arg in ("--install", "--install-block") for arg in argv)
    args, timer = None, None
    fallback = [None]
    finished = [False]
    output_lock = threading.Lock()

    def expired():
        with output_lock:
            if finished[0]:
                return
            try:
                if fallback[0]:
                    print(json.dumps(fallback[0]), flush=True)
            finally:
                os._exit(0)

    try:
        deadline = None if manual else STARTED + HOOK_SECONDS
        if not manual:
            timer = threading.Timer(max(0, deadline - time.monotonic()), expired)
            timer.daemon = True
            timer.start()
        args = parser().parse_args(argv)
        fallback[0] = warning(args)
        if not manual:
            hook_input(deadline)
        paths = paths_for(args)
        if not manual and not paths["owner"].exists():
            return 0
        url, token, identity = connection(args)
        owner = binding(args, paths, identity)
        if manual:
            install(args, paths, owner)
            return 0
        output = sync(args, paths, owner, url, token, deadline)
    except SystemExit as exc:
        return int(exc.code or 0) if manual else 0
    except Exception:
        if manual:
            print(
                "Commonplace rules installation failed; check arguments, paths and binding.",
                file=sys.stderr,
            )
            return 1
        output = fallback[0]
    finally:
        with output_lock:
            finished[0] = True
            if timer:
                timer.cancel()
    with output_lock:
        if output:
            print(json.dumps(output), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
