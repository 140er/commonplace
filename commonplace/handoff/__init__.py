"""Context handoff: hand a nearly full agent to a fresh one instead of compacting.

Off by default. When a workspace (or one agent or task) turns it on and an
agent's context passes the threshold:

1. the whole thread is stored verbatim in the workspace's Memory, chunked
   and indexed (``thread_store``, ``palace_mirror``);
2. a short handoff is written from the thread's own words (``writer``);
3. a successor agent starts with only that handoff and a pointer
   (``launchers``);
4. the successor reads the old thread on demand (``recall``, the
   ``thread_recall`` / ``thread_get`` MCP tools, the REST API).

If storing fails, no successor starts: the client compacts as usual and an
error event goes out (``service``). Design and part owners: PLAN.md in the
notes repo; contracts live in ``models``.
"""
