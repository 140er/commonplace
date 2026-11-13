### Waiting reasons and capacity at a glance

- Each waiting card keeps the reason supplied by the shared card-selection logic. A blocked card names its prerequisite; a capacity wait names the current usage and effective limit; approval and other existing waits retain their own reasons.
- Board reads and MCP board lists add a compact `waiting_summary`. It uses the same reason counts as card reads, excludes archived cards, and remains scoped to readable boards in the current workspace. Invalid cross-board blocker edges disclose neither card numbers nor titles.
- The board header and Today project status show this server sentence without reconstructing counts in the browser. With orchestration disabled, the existing card and claim behavior stays unchanged. No new table or tool is introduced.
