### Search enrichment compatibility note

The public search response deliberately omits `facts_error`, including on failed fact enrichment. A failure-only marker could distinguish hidden-present and hidden-absent worlds and break response equality. Failed enrichment therefore preserves drawers and returns the same empty facts shape as no eligible facts. The Unreleased changelog records this public-shape change, strict KG search booleans and restored argument/wing-filter compatibility; clients should no longer use `facts_error` as an availability signal.
