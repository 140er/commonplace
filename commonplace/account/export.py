"""A person's account data as one streamed ZIP archive.

Layout::

    README.txt
    account/<table>.ndjson                       control rows about the person
    workspaces/<id>/control/<table>.ndjson       control rows of an owned workspace
    workspaces/<id>/stores/<file>/<table>.ndjson every table of every workspace database
    workspaces/<id>/files/<file>                 every other workspace file, as stored
    manifest.json                                written last: entries, row counts, gaps

Rows are written as stored, one JSON object per line. Text is never
summarized or cut; bytes become ``{"$base64": ...}``. Only credentials and
their digests (:func:`tables.secret`) are replaced, in control tables.

A workspace is exported in full to each of its owners, who can already read
all of it. A workspace the person is only a member of is listed in the
manifest without its content.

Memory use does not grow with the account: rows are read in batches from
read-only snapshot connections and files in blocks, and the archive is
written straight to the response. ``manifest.json`` and the ZIP directory
come last, so a download that broke off is not a valid archive and cannot be
mistaken for a complete one.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
import zipfile
from pathlib import Path
from urllib.parse import quote as url_quote

from . import tables

FORMAT = "commonplace-account-export/1"
BATCH = 500
BLOCK = 256 * 1024
_MAGIC = b"SQLite format 3\x00"
_SIDECARS = ("-wal", "-shm", "-journal")
_DATABASE_SUFFIXES = (".sqlite3", ".sqlite", ".db")

README = """Commonplace account export

account/         what the server stores about your account
workspaces/<id>/ every workspace you own: control rows, every table of every
                 workspace database (stores/) and every other file (files/)
manifest.json    what this archive contains, with row counts

Each .ndjson file holds one JSON object per line: one stored row, unchanged.
Binary values are written as {"$base64": "..."}. Credentials and their
digests are replaced by "[redacted]". Workspaces you are a member of but do
not own are listed in manifest.json; their content belongs to their owners.
"""


class ExportUnavailable(Exception):
    """The export cannot be complete; nothing has been sent yet."""


class Sink:
    """Buffers small writes for a stream that cannot seek."""

    def __init__(self, raw, size: int = BLOCK):
        self._raw = raw
        self._size = size
        self._buffer = bytearray()

    def write(self, data) -> int:
        if self._raw is not None:
            self._buffer += data
            if len(self._buffer) >= self._size:
                self.flush()
        return len(data)

    def flush(self) -> None:
        if self._buffer and self._raw is not None:
            self._raw.write(bytes(self._buffer))
        self._buffer.clear()

    def discard(self) -> None:
        """Send nothing more: a failed archive must never get its closing directory."""
        self._raw = None
        self._buffer.clear()


def _read_only(path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    # Stored text is exported even when it is not valid UTF-8.
    conn.text_factory = lambda raw: raw.decode("utf-8", "surrogateescape")
    conn.execute("BEGIN")  # one snapshot for the whole read
    return conn


def _value(value):
    if isinstance(value, (bytes, memoryview)):
        return {"$base64": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _entry_name(name: str) -> str:
    return "/".join(url_quote(part, safe="@+,=~ ") for part in name.split("/"))


class _Archive:
    def __init__(self, sink):
        self.zip = zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED, allowZip64=True)
        self.entries: list = []
        self._stamp = time.gmtime()[:6]

    def _open(self, name):
        info = zipfile.ZipInfo(_entry_name(name), date_time=self._stamp)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o600 << 16
        return self.zip.open(info, "w", force_zip64=True)

    def text(self, name, text) -> None:
        with self._open(name) as out:
            out.write(text.encode("utf-8"))

    def rows(self, name, cursor, *, redact: bool) -> int:
        """Write a cursor as NDJSON; an empty result writes no entry."""
        columns = [d[0] for d in cursor.description]
        hidden = {c for c in columns if redact and tables.secret(c)}
        batch = cursor.fetchmany(BATCH)
        if not batch:
            return 0
        count = 0
        with self._open(name) as out:
            while batch:
                for row in batch:
                    record = {
                        c: tables.REDACTED if c in hidden and v is not None else _value(v)
                        for c, v in zip(columns, row)
                    }
                    out.write(json.dumps(record).encode("ascii") + b"\n")
                count += len(batch)
                batch = cursor.fetchmany(BATCH)
        self.entries.append({"name": _entry_name(name), "rows": count})
        return count

    def file(self, name, path) -> None:
        size = 0
        with open(path, "rb") as source, self._open(name) as out:
            while block := source.read(BLOCK):
                out.write(block)
                size += len(block)
        self.entries.append({"name": _entry_name(name), "bytes": size})


def _is_database(path: Path) -> bool:
    with open(path, "rb") as handle:
        return handle.read(len(_MAGIC)) == _MAGIC


def workspace_files(directory) -> tuple:
    """``(databases, files)`` under a workspace directory, as relative paths.

    Symbolic links are never followed. SQLite side files are skipped: a
    read-only connection reads through them. A database file that is not
    readable SQLite (an encrypted store) refuses the export.
    """
    directory = Path(directory)
    databases, files = [], []
    if not directory.is_dir() or directory.is_symlink():
        return databases, files
    for root, dirs, names in os.walk(directory, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(root) / d).is_symlink())
        for name in sorted(names):
            path = Path(root) / name
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(directory).as_posix()
            if name.endswith(_SIDECARS) and (Path(root) / name.rsplit("-", 1)[0]).is_file():
                continue
            if _is_database(path):
                databases.append(relative)
            elif name.endswith(_DATABASE_SUFFIXES) and path.stat().st_size:
                raise ExportUnavailable(f"workspace database {relative} is not readable")
            else:
                files.append(relative)
    return databases, files


def plan(gateway, user_id: str) -> dict:
    """What an export of ``user_id`` will read. Raises before anything is sent."""
    control = _read_only(gateway.db.path)
    try:
        user = control.execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise ExportUnavailable("no such account")
        memberships = control.execute(
            "SELECT w.id, w.name, m.role FROM memberships m JOIN workspaces w "
            "ON w.id = m.workspace_id WHERE m.user_id = ? ORDER BY w.created_at, w.id",
            (user_id,),
        ).fetchall()
    finally:
        control.close()
    workspaces = []
    for workspace_id, name, role in memberships:
        entry = {"id": workspace_id, "name": name, "role": role, "content": role == "owner"}
        if role == "owner":
            try:
                directory = gateway.supervisor.workspace_dir(workspace_id)
            except ValueError:
                raise ExportUnavailable(f"workspace {workspace_id} store needs repair") from None
            entry["directory"] = directory
            entry["databases"], entry["files"] = workspace_files(directory)
        workspaces.append(entry)
    return {"user": user[0], "email": user[1], "workspaces": workspaces}


def write(gateway, planned: dict, raw) -> dict:
    """Stream the archive for a :func:`plan` to ``raw``. Returns the manifest."""
    sink = Sink(raw)
    try:
        return _write(gateway, planned, sink)
    except BaseException:
        # ZipFile finishes an open archive when it is collected.
        sink.discard()
        raise


def _write(gateway, planned: dict, sink) -> dict:
    archive = _Archive(sink)
    scope = {"uid": planned["user"], "email": planned["email"]}
    control = _read_only(gateway.db.path)
    try:
        present = tables.present(control)
        unknown = tables.unclassified(present)
        rules = dict(tables.RULES)
        rules.update({name: tables.guessed(present[name]) for name in unknown})
        archive.text("README.txt", README)
        for name in present:
            clause = tables.account_clause(name, present, rules=rules)
            if clause is not None:
                cursor = control.execute(
                    f"SELECT * FROM {tables.quote(name)} WHERE {clause}", scope
                )
                archive.rows(f"account/{name}.ndjson", cursor, redact=True)
        listed = []
        for workspace in planned["workspaces"]:
            listed.append({k: workspace[k] for k in ("id", "name", "role", "content")})
            if not workspace["content"]:
                continue
            prefix = f"workspaces/{workspace['id']}"
            for name in present:
                clause = tables.workspace_clause(name, present, rules=rules)
                if clause is not None:
                    cursor = control.execute(
                        f"SELECT * FROM {tables.quote(name)} WHERE {clause}",
                        {"wid": workspace["id"]},
                    )
                    archive.rows(f"{prefix}/control/{name}.ndjson", cursor, redact=True)
            directory = Path(workspace["directory"])
            for relative in workspace["databases"]:
                _store(archive, f"{prefix}/stores/{relative}", directory / relative)
            for relative in workspace["files"]:
                archive.file(f"{prefix}/files/{relative}", directory / relative)
    finally:
        control.close()
    manifest = {
        "format": FORMAT,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user": planned["user"],
        "email": planned["email"],
        "workspaces": listed,
        # Tables newer than this exporter: exported on a best reading.
        "unclassified_tables": unknown,
        "complete": not unknown,
        "redacted": tables.REDACTED,
        "entries": archive.entries,
    }
    archive.text("manifest.json", json.dumps(manifest, indent=2))
    archive.zip.close()
    sink.flush()
    return manifest


def _store(archive, prefix, path) -> None:
    conn = _read_only(path)
    try:
        names = [
            row[0]
            for row in conn.execute(
                # Virtual tables keep their rows in ordinary shadow tables.
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND sql NOT LIKE 'CREATE VIRTUAL%' ORDER BY name"
            )
        ]
        for name in names:
            cursor = conn.execute(f"SELECT * FROM {tables.quote(name)}")
            # Sealed credentials of a chat-platform install and the hashes of its link codes are
            # write-only, also here.
            archive.rows(
                f"{prefix}/{name}.ndjson",
                cursor,
                redact=name in ("bridge_installs", "bridge_link_codes"),
            )
    finally:
        conn.close()
