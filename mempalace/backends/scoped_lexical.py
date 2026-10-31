"""One-call FTS scope: bounded term fetches and local ranking, without caches."""

import logging
from contextlib import contextmanager

from ..lexical import bm25_scores, lexical_query_terms, tokenize
from ..stored_text import INDEX_SPANS, decode_record, index_text
from .base import LexicalHit, LexicalResult

logger = logging.getLogger(__name__)


class ScopedFTS:
    def __init__(self, conn, table, scope_sql, params, load_texts, hydrate):
        self.conn = conn
        self.table = table
        self.load_texts = load_texts
        self.hydrate = hydrate
        self.scope_sql, self.scope_params = scope_sql, params
        # The cutoff saturates at 40k scoped records; counting beyond that
        # cannot change the accepted terms or their ranking.
        self.count = conn.execute(
            f"SELECT count(*) FROM ({scope_sql} LIMIT 40000)", params
        ).fetchone()[0]
        if self.count <= 20000:
            conn.execute("CREATE TEMP TABLE lexical_scope(id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO lexical_scope " + scope_sql, params)

    def fetch(self, term, limit):
        if self.count <= 256:
            if not self.count:
                return []
            low, high = self.conn.execute("SELECT min(id),max(id) FROM lexical_scope").fetchone()
            predicate = f"rowid >= ? AND rowid <= ? AND EXISTS (SELECT 1 FROM lexical_scope WHERE lexical_scope.id={self.table}.rowid)"
            params = (low, high)
        elif self.count <= 20000:
            predicate = (
                f"EXISTS (SELECT 1 FROM lexical_scope WHERE lexical_scope.id={self.table}.rowid)"
            )
            params = ()
        else:
            predicate = f"EXISTS (SELECT 1 FROM ({self.scope_sql}) allowed WHERE allowed.id={self.table}.rowid)"
            params = self.scope_params
        return [
            row[0]
            for row in self.conn.execute(
                f"SELECT rowid FROM {self.table} WHERE {self.table} MATCH ? AND {predicate} "
                "ORDER BY rowid DESC LIMIT ?",
                ('"' + term.replace('"', '""') + '"', *params, limit),
            )
        ]


@contextmanager
def lexical_reader(path):
    from ._inproc_sqlite import open_reader

    conn = open_reader(path)
    try:
        conn.execute("BEGIN")
        yield conn
    finally:
        conn.close()


def scope_local_search(
    collection, query, n_results, where=None, *, closet=False, stop_words=frozenset()
):
    terms, skip = lexical_query_terms(
        query,
        stop_words,
        min_length=getattr(collection, "lexical_min_token_length", 2),
        trigram=getattr(collection, "lexical_min_token_length", 2) == 3,
        closet=closet,
    )
    if skip:
        return LexicalResult(hits=[], skip_reason=skip)
    with collection.lexical_scope(where) as scope:
        cutoff = min(2000, max(1000, scope.count // 20))
        selected, ids = [], set()
        for term in terms:
            matches = scope.fetch(term, 5000 if closet else cutoff + 1)
            if closet and len(matches) >= 5000:
                # No partial closet pool: its ordering would change boosts.
                logger.debug("Closet match limit reached; boosts disabled")
                return LexicalResult(hits=[])
            if matches and (closet or len(matches) <= cutoff):
                selected.append(term)
                ids.update(matches)
                if not closet and len(selected) == 8:
                    break
        # Every accepted term's complete match set is present. Drawer pools
        # contain at most 8*2000 ids; closets retain the complete capped query.
        ordered = sorted(ids)
        texts = scope.load_texts(ordered)
        rank_query = (
            " ".join(term[:64] for term in tokenize(query)[:256]) if closet else " ".join(selected)
        )
        scores = bm25_scores(rank_query, [texts.get(row_id, "") for row_id in ordered])
        ranked = sorted(zip(ordered, scores), key=lambda pair: -pair[1])[:n_results]
        hydrated = scope.hydrate([row_id for row_id, _ in ranked])
        return LexicalResult(
            hits=[
                LexicalHit(
                    id=hydrated[row_id][0],
                    document=hydrated[row_id][1],
                    metadata=hydrated[row_id][2],
                    score=score,
                )
                for row_id, score in ranked
                if row_id in hydrated
            ],
            selected_terms=selected,
        )


@contextmanager
def sqlite_collection_scope(collection, where=None, *, compact=False):
    import json

    from .sqlite_exact import _equality_where_sql

    pushed = _equality_where_sql(where, locus_columns=collection._handle.has_locus_columns)
    if pushed is None:
        raise ValueError("lexical filter cannot be bounded")
    filter_sql, params = pushed
    path = collection._backend._db_path(collection._handle.palace_path)
    with lexical_reader(path) as conn:
        cid = conn.execute(
            "SELECT id FROM collections WHERE name=?", (collection._collection_name,)
        ).fetchone()[0]
        synced = conn.execute("SELECT value FROM meta WHERE key='fts5_rowid_synced'").fetchone()
        aligned = compact or (synced and synced[0] == "1")
        if aligned:
            scope_sql = (
                f"SELECT rowid AS id FROM documents WHERE collection_id=? AND ({filter_sql})"
            )
        else:
            scope_sql = (
                "SELECT docs_fts.rowid AS id FROM documents JOIN docs_fts ON documents.id=docs_fts.doc_id "
                f"AND documents.collection_id=docs_fts.collection_id WHERE documents.collection_id=? AND ({filter_sql})"
            )
        raw = {}

        def load(ids):
            for start in range(0, len(ids), 900):
                chunk = ids[start : start + 900]
                marks = ",".join("?" for _ in chunk)
                dictionary = ",dictionary_id" if compact else ""
                if aligned:
                    rows = conn.execute(
                        f"SELECT rowid,id,document,metadata_json{dictionary} FROM documents WHERE collection_id=? AND rowid IN ({marks})",
                        (cid, *chunk),
                    )
                else:
                    rows = conn.execute(
                        "SELECT f.rowid,d.id,d.document,d.metadata_json FROM documents d JOIN docs_fts f ON d.id=f.doc_id AND d.collection_id=f.collection_id "
                        + f"WHERE d.collection_id=? AND f.rowid IN ({marks})",
                        (cid, *chunk),
                    )
                for row in rows:
                    row_id, doc_id, document, metadata = row[:4]
                    if compact:
                        document = collection._decompress(conn.cursor(), document, row[4])
                    raw[row_id] = (doc_id, document or "", metadata)

        def load_texts(ids):
            load(ids)
            return {
                row_id: index_text(
                    raw[row_id][1],
                    {INDEX_SPANS: json.loads(raw[row_id][2]).get(INDEX_SPANS)}
                    if INDEX_SPANS in raw[row_id][2]
                    else {},
                )
                for row_id in ids
                if row_id in raw
            }

        def hydrate(ids):
            return {
                row_id: (raw[row_id][0], *decode_record(raw[row_id][1], json.loads(raw[row_id][2])))
                for row_id in ids
                if row_id in raw
            }

        yield ScopedFTS(conn, "docs_fts", scope_sql, (cid, *params), load_texts, hydrate)
