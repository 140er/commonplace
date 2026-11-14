- **Owns:**

  - `bots:learned` is a third person-granted app scope next to `bots:chat` and `bots:confirm`: OAuth consent shows its own unticked box (only when requested together with `bots:chat`), `create_code` mints it only for a person with chat, and the session policy never carries it.
