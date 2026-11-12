### `mcp:` source IDs in fact sources, tunnel labels and drawer provenance

- Follow-up of the room rule for threads (card #414). Before, a knowledge-graph fact with `source_thread: "mcp:<id>"`, a tunnel label such as `see mcp:<id> notes`, and a drawer's `source_thread` or `source_kept.thread` reached readers who cannot read that thread, because those paths only looked for `thr_` IDs and capture references.

- The same check as for rooms (`private_thread_reference`: whole value, prefix-matched, every matching thread readable, hidden on a lookup fault) now runs on the provenance fields that name a thread and on tunnel labels. A reader who cannot read every matching thread loses the field, or the tunnel for a label; a value naming no stored thread is unchanged.

- Residual: none new. A legacy non-leading twin source can still hide a value naming a canonical thread, as for rooms.
