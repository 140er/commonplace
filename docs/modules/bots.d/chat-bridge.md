- **Owns:**

  - A turn has a surface (card #455, slice 3). `TurnRun` and `tools.Turn` take `surface` (`web` by default, or `dm` / `channel` from the chat bridge) and `disclosure` (`titles` or `full`, channel only); the web path sends exactly what it sent before. `tools.surface_allows` narrows the tools offered: `dm` gets no routine tool (writes wait for the confirmation slice; confirm tools leave their chip in the person's web thread), `channel` gets read tools of the one project only, with `titles` just `project_status` and `list_work`. A channel turn also gets no memory notes, no earlier messages, no learned items and no persona instructions, the system prompt says the reply is public to the channel, and no learning runs after it.
- **Tests:**

  - The bridge's `tests/test_chat_bridge_intake.py` shows the offered tools and the model input per surface; the web path is covered by the existing bots turn tests.
