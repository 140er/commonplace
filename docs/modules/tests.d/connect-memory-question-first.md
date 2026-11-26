- **Owns:**

  - `tests/test_projects_connect_ui.py` covers the memory question coming before any Connect request (no error text, own section focused, two requests to the token screen, dismissal sends nothing) and the stale-view refusal; `tests/test_projects_connect_edges.py` covers the server refusing a direct Connect on shared memory with a clear message and no token.
