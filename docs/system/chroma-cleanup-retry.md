### Deferred Chroma cleanup retry

Closing a Chroma client or releasing a palace guard can be deferred to a
background worker. If a cleanup pass fails, the worker logs the failure, keeps
its slot and retries with exponential backoff capped at one second until the
queues drain, so queued cleanup no longer waits for another caller to schedule
a new worker.
