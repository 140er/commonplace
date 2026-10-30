"""Local lexical candidates for facts; source text stays in its original store."""

import json

from . import fact_provenance


def install(conn):
    """Build once, then maintain the index atomically with the existing writes.

    SQL triggers also cover low-level writes and entity renames. The index is
    derived data: no import/replication logic or verbatim source is changed.
    """
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='fact_search'").fetchone():
        return
    conn.execute("SAVEPOINT fact_search_install")
    try:
        _create(conn)
    except Exception:
        conn.execute("ROLLBACK TO fact_search_install")
        raise
    finally:
        conn.execute("RELEASE fact_search_install")


def _create(conn):
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS fact_search USING fts5("
        "subject, predicate, object, tokenize='unicode61 remove_diacritics 0')"
    )
    select = (
        "SELECT t.rowid, s.name, replace(t.predicate, '_', ' '), o.name "
        "FROM triples t JOIN entities s ON s.id=t.subject JOIN entities o ON o.id=t.object"
    )
    conn.execute("INSERT OR REPLACE INTO fact_search(rowid, subject, predicate, object) " + select)
    for event, before in (
        ("INSERT", ""),
        ("UPDATE OF subject, predicate, object", "DELETE FROM fact_search WHERE rowid=old.rowid;"),
    ):
        name = "insert" if event == "INSERT" else "update"
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS fact_search_{name} AFTER {event} ON triples BEGIN "
            + before
            + "INSERT OR REPLACE INTO fact_search(rowid, subject, predicate, object) "
            + select
            + " WHERE t.rowid=new.rowid; END"
        )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS fact_search_delete AFTER DELETE ON triples BEGIN "
        "DELETE FROM fact_search WHERE rowid=old.rowid; END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS fact_search_rename AFTER UPDATE OF name ON entities "
        "WHEN old.name IS NOT new.name BEGIN "
        "DELETE FROM fact_search WHERE rowid IN "
        "(SELECT rowid FROM triples WHERE subject=new.id OR object=new.id); "
        "INSERT OR REPLACE INTO fact_search(rowid, subject, predicate, object) "
        + select
        + " WHERE t.subject=new.id OR t.object=new.id; END"
    )


def candidates(conn, tokens, visible_sql, visible_params, temporal_sql="", temporal_params=()):
    """One indexed SELECT, including all observations, without a pre-ranking cut."""
    columns = (
        "source_thread",
        "source_seq_start",
        "source_seq_end",
        "source_drawer_id",
        "source_file",
        "added_at",
        *fact_provenance.COLUMNS,
    )
    source_json = ", ".join(f"'{key}', fs.{key}" for key in columns)
    rows = conn.execute(
        "SELECT t.*, s.name AS subject_name, o.name AS object_name, "
        f"json_group_array(json_object({source_json})) "
        "FILTER (WHERE fs.id IS NOT NULL) AS sources_json "
        "FROM fact_search JOIN triples t ON t.rowid=fact_search.rowid "
        "JOIN entities s ON s.id=t.subject JOIN entities o ON o.id=t.object "
        "LEFT JOIN fact_sources fs ON fs.triple_id=t.id "
        "WHERE fact_search MATCH ?" + visible_sql + temporal_sql + " GROUP BY t.rowid",
        [
            " OR ".join('"' + word + '"' for word in sorted(tokens)),
            *visible_params,
            *temporal_params,
        ],
    ).fetchall()
    return [{**dict(row), "sources": json.loads(row["sources_json"])} for row in rows]
