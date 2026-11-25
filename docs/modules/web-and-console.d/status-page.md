- **Owns:**

  - The read-only capabilities and status page (`web/static/status.html`, `status.js`, `status-page.js`, `status.css`) and its one route `GET /api/status` (`web/status.py`), linked from Settings. It lists skills (name, one-line description, current scope), chat-bridge and meeting-notes status, and the cost summary with its period and scope.
- **Promises:**

  - The route is read-only and adds no permission: it is for a signed-in person only (a token or machine gets 403), and each section reuses the existing reader with its own owner and scope checks (skill shares, owner-only bridge installs, own meetings, the cost sums a person can read). Skill text, credentials and meeting text never appear; only a derived one-line description does.
  - Every section that is empty, switched off, restricted or unreadable carries a plain `reason`, and the page renders an unknown or malformed answer as an explanation, never an empty panel. A bridge time is shown as "last seen" and the page says it is not proof that anything is running. Only a 401 reads as signed out; a timeout or other failure reads as a failed read with a retry.
- **How it fails:**

  - A section that raises is logged and answers `error` with a fixed reason while the other sections still render; the error text is never sent to the browser. Responses are escaped by the page, and a late answer to an earlier read cannot replace a newer one.
