# Review comment originals

An original-word read returns the comment id, stored revision and verbatim body from the same row, after the normal card visibility check. The response grants no additional permission.

The card caches a reveal by comment id and revision. Polling the same revision keeps it; an edit discards it. A late response cannot repaint or announce a reveal for an edited, hidden or closed card. If the response already carries a newer revision than the displayed comment, the card refreshes and asks the reader to reveal that revision explicitly.
