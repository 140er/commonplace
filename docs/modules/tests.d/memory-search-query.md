- **Tests:**

  - `tests/test_workbench_memory.py` exercises typed form submission, filter changes and empty-query validation through the Memory controller. `tests/test_memory_search_browser.py` covers both looks at desktop and phone sizes, query retention through every filter, focus restoration and existing private-source permissions for owners and members.

  - Browser coverage skips when the optional browser package is absent, matching the other browser tests. The typed-form controller regression uses Node and still runs in ordinary checks without that package.
