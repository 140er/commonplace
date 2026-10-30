"""Local lexical candidates for facts; source text stays in its original store."""

import json

from . import fact_provenance


def install(conn):
    """Atomically build or upgrade the derived index without changing facts.

    FTS document IDs come from an INTEGER PRIMARY KEY mapping to the stable
    triple ID. VACUUM can renumber triples.rowid without affecting this index.
    """
    ready = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE name IN "
        "('fact_search', 'fact_search_keys', 'fact_search_vocab')"
    ).fetchone()[0]
    if ready == 3:
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
    for name in ("insert", "update", "delete", "rename"):
        conn.execute("DROP TRIGGER IF EXISTS fact_search_" + name)
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
    """One SQL query: bounded BM25 seeds, scoped key history, sources and IDF.

    FTS5's rank column uses bm25 by default. ORDER BY rank LIMIT bounds the
    seed set before expanding its (subject, predicate) keys or loading sources.
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
    rows = conn.execute(
        "WITH words(term) AS (VALUES " + ",".join("(?)" for _ in words) + "), "
        "term_stats AS MATERIALIZED ("
        "SELECT words.term, COALESCE(v.doc, 0) AS doc FROM words "
        "LEFT JOIN fact_search_vocab v ON v.term=words.term), "
        "stats AS MATERIALIZED ("
        "SELECT json_group_object(term, doc) AS frequencies, "
        "(SELECT count(*) FROM fact_search_keys) AS documents FROM term_stats), "
        "chosen_terms AS MATERIALIZED ("
        "SELECT term FROM term_stats WHERE doc <= ?*(SELECT documents FROM stats) "
        "OR (SELECT min(doc) FROM term_stats) > ?*(SELECT documents FROM stats)), "
        "selected AS MATERIALIZED ("
        "SELECT t.subject, t.predicate FROM fact_search "
        "JOIN triples t ON t.id=fact_search.triple_id "
        "WHERE fact_search MATCH (SELECT group_concat('\"'||term||'\"', ' OR ') FROM chosen_terms)"
        + visible_sql
        + temporal_sql
        + " ORDER BY fact_search.rank LIMIT ?), "
        "keys AS MATERIALIZED (SELECT DISTINCT subject, predicate FROM selected) "
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
