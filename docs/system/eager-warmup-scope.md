### Eager warmup scope

`MEMPALACE_EAGER_WARMUP` is opt-in for directly launched workers (stdio or a local server). The gateway does not forward it to workspace workers; setting it on the gateway or container has no effect on their startup. Gateway workers retain lazy loading, avoiding a full cold index load at worker start.
