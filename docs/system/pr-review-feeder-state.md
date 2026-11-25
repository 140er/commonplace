### Feeder cache publication and actionable context

The review-comment reader writes its cache into a unique private staging file,
flushes its buffered contents and synchronizes that file before atomically
replacing the prior cache. A failed flush or replacement preserves the previously
published cache and removes only that attempt's staging file. This is a cache of
reads, not authority for review decisions or a guarantee against loss of an
entire filesystem. Existing complete comment reads already prune removed ids.

The Coworker's start context selects new and working comments in SQL before
applying its 25-comment limit. Earlier resolved comments cannot hide later work.
Other comment lists retain their existing statuses, ordering and pagination.
Card visibility, revision and answer binding, round limits and report
transactions retain their existing checks.
