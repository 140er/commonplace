### Tool surface snapshot includes the benchmark tools

The standard and full agent profiles show the three owner benchmark tools (get, promote, rollback)
added with subscription replay. The pinned surface snapshot records them so the contract test
matches what agents actually see; the slim profile does not show them.

The benchmark promote and rollback writes are checked writes, like the other owner config writes
(`orch_config_accept`): they go through the same fresh-rules check as every other gateway write.
