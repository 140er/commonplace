- **Promises:** The status overview takes the worker's diagnostics through a fixed vocabulary
  and drops the worker's transport decorations, including the free-space fields
  (`data_free_bytes`, `min_free_bytes`, `disk_low`, `writes_refused`, `log_write_failures`), so
  a worker that reports them still gives the same scoped drawer counts.
