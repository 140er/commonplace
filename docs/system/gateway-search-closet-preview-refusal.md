### Gateway search closet preview refusal

- Authenticated gateway search removes closet_preview from every result, including
  readable closets. Matching a drawer's source_file does not prove the closet's
  wing and room, so the gateway omits the preview without extending the worker
  descriptor protocol. Local unscoped search retains its existing previews.
- This changes the outgoing preview field only; ranking and closet boosts keep
  their existing behavior. It is not a new claim of hidden-independent ranking.
- Stored citations and fact text remain unchanged. Ordinary reference-spelling
  guards retain token-free file citations only with exact readable local proof
  under locked D9.1-A; unresolved plain files are omitted on reads.
- Stable result identifiers remain visible when file citations are omitted. Some
  asset ids hash the resolved path, and some drawer ids derive from the source
  path and chunk index. Guessing a path can therefore confirm an identity even
  without its citation. This identity residual is documented alongside the
  accepted shared-path presence residual; citation omission does not anonymize
  stable ids or grant additional content access.
