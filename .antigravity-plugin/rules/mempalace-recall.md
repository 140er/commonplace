# MemPalace recall

Search the palace before answering anything earlier sessions may have
covered: decisions, why or who, unfinished work, warnings about this repo
or machine ("anything I should know about X?", "safe way to Y?"), and
before saying "I don't know":

1. `mempalace_search` with a short keyword query (`mempalace_kg_query`
   for relational or time-bound facts).
2. Quote stored text **verbatim**; never summarize it.
3. Nothing after widening once, or MCP down: say so in one line, then
   answer from the repo (code, `git log`) and label it. A commit is not
   a decision. Never answer from model memory.

Skip greenfield edits (renaming a variable, fixing a typo).

Full protocol: <https://github.com/MemPalace/mempalace/blob/main/integrations/shared/recall-protocol.md>. Deeper guidance:
the `mempalace-recall` skill.
