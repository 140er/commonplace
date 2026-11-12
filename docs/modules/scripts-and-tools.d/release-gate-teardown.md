- **Promises:**

  - The release gate's throwaway instance closes its worker supervisor and handoff service on exit, not just stops the workers, so no memory worker process or `handoff-mirror` thread outlives a test that used it.
