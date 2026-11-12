### Ranking statistics stay inside the readable scope

- Fact and vector-disabled drawer ranking for a restricted caller use statistics of the readable scope only (document count, term frequencies, candidate pool); FTS5 rank and whole-index vocabulary counts are used only when the caller can read everything. Hidden facts or drawers therefore cannot change a visible order or score.
