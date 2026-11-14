### HTTP startup observation tests run as the child generation

- `_run_http_loop` has two roles. Without the child marker it hands off to the restart supervisor, which launches the real server process and blocks for as long as it lives. With the marker it is the child generation that activates worker observation, runs the startup preflight and serves.
- The startup-ordering tests in `tests/test_worker_observation_durable_begin.py` assert on the child path, but did not set the marker after the supervisor landed. They spawned a real server and hung instead of recording phases, which made any run that selected them hit the time ceiling.
- An autouse fixture in that file now sets the child marker, clears the coordinator and swaps the supervisor factory for a stub that raises, so a missing marker fails in milliseconds. Production behaviour is unchanged: observation still activates before the startup preflight.
