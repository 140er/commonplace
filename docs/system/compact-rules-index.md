### Compact rules index

- `session_start rules=index` and `rules_get effective=true format=index` list each effective rule as key, section, scope, size of the full text and a short quoted title (the owner summary, else the first sentence), so a large rule set costs a few kilobytes instead of the whole text.
- `rules_get keys=[...]` returns the selected rules in full, one key-labelled line each, and names unknown keys. Both are partial reads: they never record the set as served and give no `rules_seq`, so a checked connection must still read the full set before a write. In warn or enforce mode the index says so.
- The default of both tools is unchanged, and replies stay within the existing reply size bound.
