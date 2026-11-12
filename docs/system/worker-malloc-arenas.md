### Worker malloc arenas

Each memory worker the gateway launches starts with `MALLOC_ARENA_MAX=2`. A
non-empty `MALLOC_ARENA_MAX` in the gateway's own environment passes through
unchanged; `0` restores glibc's default and an empty value counts as unset.
Non-glibc platforms ignore the variable.

The worker serves every request on a new thread. Under concurrent calls glibc
gives those threads their own malloc arenas, up to its default of eight per core,
and keeps the pages freed into each until a trim. The cap bounds that allocator
retention and nothing else. On a synthetic 2,500-drawer palace (MiniLM
embedder) with sixteen concurrent clients, a worker without the cap grew from
about 713 to 998 MiB anonymous RSS after warm-up over 4,800 calls and was still
rising; with the cap the same mix grew from about 385 to 406 MiB. Wall time
was 0.5 to 5.4% longer with the cap in three measured pairs. These are local
harness figures, not production measurements.

The cap does not change the memory a worker takes when it first loads a palace's
index and embedding model, which scales with the palace and the model, and it
does not touch the gateway process. Whether it resolves production worker OOMs
is not established: that needs a deploy and a check of the worker's RSS
afterwards.
