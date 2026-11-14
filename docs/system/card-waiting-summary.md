### Waiting reasons and capacity at a glance

- Each waiting card keeps the reason supplied by the shared card-selection logic. A blocked card names its prerequisite; a capacity wait names the current usage and effective limit; approval and other existing waits retain their own reasons.
- Board reads and MCP board lists add a compact `waiting_summary`. It uses the same reason counts as card reads, excludes archived cards, and remains scoped to readable boards in the current workspace. Invalid cross-board blocker edges disclose neither card numbers nor titles.
- The board header and Today project status show server sentences without reconstructing counts in the browser. Today uses task vocabulary; Deck summaries stay neutral and its phone board keeps 16px gutters and a visible project title. Classic rendering is unchanged.
- Today offers a named bot destination or a No bots yet state with the existing New bot form. It never offers an empty picker. With orchestration disabled, the existing card and claim behavior stays unchanged. No new table or tool is introduced.
- Read-only administrative metadata rejects a damaged main database before opening SQLite, including when a surviving WAL could otherwise supply old rows. An absent source remains unconfigured without being created or initialized.
