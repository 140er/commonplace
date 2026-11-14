- **Promises:**

  - File association selectors require a nonblank decoded identity, including direct worker requests; snapshots match both stored spelling and decoded identity and deduplicate physical row ids.

- **How it fails:**

  - A blank or whitespace-only decoded selector, a missing selector or an unavailable complete snapshot returns no proof before backend access; unsupported snapshot backends fail closed.

- **Tests:**

  - Worker selector guards cover tuple and transported-list forms, whitespace and empty decoded identities, union revalidation and identical-selector deduplication.
