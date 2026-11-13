- **Promises:**

  - A tunnel label stays readable when it holds an emoji joiner or a literal percent sign: `commonplace/handoff/tunnels.py` removes control and format characters before the `mcp:` source-ID check, and an ID split by a joiner is still hidden.
