- **Promises:** `GET /api/admin/instance/deploy` also returns `data_free_bytes` (free bytes
  on the data volume, `null` when unknown) and `disk_low` (true below the free-space floor, 256 MiB unless `MEMPALACE_MIN_FREE_BYTES` says
  otherwise),
  so the instance admin sees a filling disk before writes are refused. The public `/healthz`
  stays `{"ok":true}`.
