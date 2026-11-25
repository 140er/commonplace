### Backend declarations preserve absent metadata

Readable metadata sequences describe existing `None` entries without rewriting them or removing rows. Concrete results remain lists. Native schema parsing keeps its existing defaults and validation, and the palace lock's static export adds no runtime fragment import.

The embedding wrapper also accepts explicit boolean writes to `upsert_merges_metadata` and forwards them to the inner collection. This is an assignment compatibility extension; constructing or reading a wrapper does not change the inner capability or its metadata merge behavior.

Compact updates keep their existing handling of explicit vectors and keyword-only records. Its metadata helpers explicitly retain their previous `None` exception rather than accepting an absent metadata dictionary.
