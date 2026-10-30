"""Local lexical candidates for facts; source text stays in its original store."""

import json
import logging

from . import fact_provenance


def _drop_triggers(conn):
    for name in ("insert", "update", "delete", "rename"):
        conn.execute("DROP TRIGGER IF EXISTS fact_search_" + name)


def try_install(conn):
    """An optional index must never prevent ordinary KG reads or writes.

    A failed atomic build leaves user data untouched. Disable any old index
    hooks too, so writes can proceed even if the FTS module is unavailable.
    Missing hooks ensure the next graph open retries the build.
    """
    try:
        install(conn)
        return True
    except Exception:
        _drop_triggers(conn)
        logging.getLogger(__name__).warning(
            "Fact search index unavailable; will retry on next graph open"
        )
        return False


def install(conn):
    """Atomically build or upgrade the derived index without changing facts.

    FTS document IDs come from an INTEGER PRIMARY KEY mapping to the stable
    triple ID. VACUUM can renumber triples.rowid without affecting this index.
    """
    ready = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE "
        "(type='table' AND name IN ('fact_search', 'fact_search_keys', 'fact_search_vocab')) "
        "OR (type='trigger' AND name IN "
        "('fact_search_insert', 'fact_search_update', 'fact_search_delete', 'fact_search_rename')) "
        "OR (type='index' AND name='idx_fact_search_key')"
    ).fetchone()[0]
    if ready == 8:
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
    _drop_triggers(conn)
    for name in ("fact_search_vocab", "fact_search", "fact_search_keys"):
        conn.execute("DROP TABLE IF EXISTS " + name)
    conn.execute(
        "CREATE TABLE fact_search_keys (docid INTEGER PRIMARY KEY, triple_id TEXT NOT NULL UNIQUE)"
    )
    conn.execute("INSERT INTO fact_search_keys(triple_id) SELECT id FROM triples")
    conn.execute(
        "CREATE VIRTUAL TABLE fact_search USING fts5("
        "triple_id UNINDEXED, subject, predicate, object, "
        "tokenize='unicode61 remove_diacritics 0')"
    )
    conn.execute("CREATE VIRTUAL TABLE fact_search_vocab USING fts5vocab(fact_search, 'row')")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fact_search_key ON triples(subject, predicate)")
    select = (
        "SELECT k.docid, t.id, s.name, replace(t.predicate, '_', ' '), o.name "
        "FROM triples t JOIN fact_search_keys k ON k.triple_id=t.id "
        "JOIN entities s ON s.id=t.subject JOIN entities o ON o.id=t.object"
    )
    insert = "INSERT OR REPLACE INTO fact_search(rowid, triple_id, subject, predicate, object) "
    conn.execute(insert + select)
    for event, before in (
        ("INSERT", ""),
        (
            "UPDATE OF id, subject, predicate, object",
            "DELETE FROM fact_search WHERE rowid=(SELECT docid FROM fact_search_keys WHERE triple_id=old.id); "
            "DELETE FROM fact_search_keys WHERE triple_id=old.id AND old.id!=new.id; ",
        ),
    ):
        name = "insert" if event == "INSERT" else "update"
        conn.execute(
            f"CREATE TRIGGER fact_search_{name} AFTER {event} ON triples BEGIN "
            + before
            + "INSERT INTO fact_search_keys(triple_id) SELECT new.id WHERE NOT EXISTS "
            "(SELECT 1 FROM fact_search_keys WHERE triple_id=new.id); "
            + insert
            + select
            + " WHERE t.id=new.id; END"
        )
    conn.execute(
        "CREATE TRIGGER fact_search_delete AFTER DELETE ON triples BEGIN "
        "DELETE FROM fact_search WHERE rowid=(SELECT docid FROM fact_search_keys WHERE triple_id=old.id); "
        "DELETE FROM fact_search_keys WHERE triple_id=old.id; END"
    )
    conn.execute(
        "CREATE TRIGGER fact_search_rename AFTER UPDATE OF name ON entities "
        "WHEN old.name IS NOT new.name BEGIN "
        "DELETE FROM fact_search WHERE rowid IN "
        "(SELECT k.docid FROM fact_search_keys k JOIN triples t ON t.id=k.triple_id "
        "WHERE t.subject=new.id OR t.object=new.id); "
        + insert
        + select
        + " WHERE t.subject=new.id OR t.object=new.id; END"
    )


def candidates(
    conn,
    tokens,
    visible_sql,
    visible_params,
    temporal_sql="",
    temporal_params=(),
    *,
    limit,
    common_term_fraction,
):
    """One SQL query: distinct BM25 seed keys, scoped history, sources and IDF.

    FTS5's rank column uses bm25 by default. Each key has one seed score (its
    best match), so history cannot consume another key's slot in the seed cap.
    History expansion applies the same scope and as-of predicates as the seeds.
    """
    words = sorted(tokens)
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
    match_sql = (
        "SELECT t.subject, t.predicate, fact_search.rank AS relevance FROM fact_search "
        "JOIN triples t ON t.id=fact_search.triple_id "
        "WHERE fact_search MATCH COALESCE("
        "(SELECT group_concat('\"'||term||'\"', ' OR ') FROM chosen_terms), '\"\"')"
        + visible_sql
        + temporal_sql
    )
    rows = conn.execute(
        "WITH words(term) AS (VALUES " + ",".join("(?)" for _ in words) + "), "
        "term_stats AS MATERIALIZED ("
        "SELECT words.term, COALESCE(v.doc, 0) AS doc FROM words "
        "LEFT JOIN fact_search_vocab v ON v.term=words.term), "
        "stats AS MATERIALIZED ("
        "SELECT json_group_object(term, doc) AS frequencies, "
        "(SELECT count(*) FROM fact_search_keys) AS documents FROM term_stats), "
        "chosen_terms AS MATERIALIZED ("
        "SELECT term FROM term_stats WHERE doc > 0 AND "
        "(doc <= ?*(SELECT documents FROM stats) "
        "OR (SELECT min(doc) FROM term_stats WHERE doc > 0) > ?*(SELECT documents FROM stats))), "
        "first_matches AS MATERIALIZED (" + match_sql + " ORDER BY fact_search.rank LIMIT ?), "
        "first_keys AS MATERIALIZED (SELECT DISTINCT subject, predicate FROM first_matches), "
        "extra_matches AS MATERIALIZED ("
        + match_sql
        + " AND (SELECT count(*) FROM first_keys) < (SELECT count(*) FROM first_matches) "
        "AND NOT EXISTS (SELECT 1 FROM first_keys k "
        "WHERE k.subject=t.subject AND k.predicate=t.predicate)), "
        "extra_keys AS MATERIALIZED ("
        "SELECT subject, predicate FROM extra_matches GROUP BY subject, predicate "
        "ORDER BY min(relevance), subject, predicate "
        "LIMIT ? - (SELECT count(*) FROM first_keys)), "
        "keys AS MATERIALIZED (SELECT * FROM first_keys UNION ALL SELECT * FROM extra_keys) "
        "SELECT t.*, s.name AS subject_name, o.name AS object_name, "
        "stats.frequencies, stats.documents, "
        f"json_group_array(json_object({source_json})) "
        "FILTER (WHERE fs.id IS NOT NULL) AS sources_json "
        "FROM keys k JOIN triples t INDEXED BY idx_fact_search_key "
        "ON t.subject=k.subject AND t.predicate=k.predicate "
        "JOIN entities s ON s.id=t.subject JOIN entities o ON o.id=t.object "
        "CROSS JOIN stats LEFT JOIN fact_sources fs ON fs.triple_id=t.id "
        "WHERE 1" + visible_sql + temporal_sql + " GROUP BY t.id",
        [
            *words,
            common_term_fraction,
            common_term_fraction,
            *visible_params,
            *temporal_params,
            limit,
            *visible_params,
            *temporal_params,
            limit,
            *visible_params,
            *temporal_params,
        ],
    ).fetchall()
    return [
        {
            **dict(row),
            "sources": json.loads(row["sources_json"]),
            "frequencies": json.loads(row["frequencies"]),
        }
        for row in rows
    ]
