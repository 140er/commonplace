- **Tests:**

  - Browser-free Memory contracts count requests per query (one note request with limit 25, none per room, at most two in flight), check the abort when the query changes or Memory closes, the timeout error with Try again, the distinction between an empty result and an error, the loading line, "Show more" and partial-failure retry. A mutation of each rule makes its case fail.
  - Deck contracts check that typing sends one search per pause and that a newer query aborts the older request. Optional browser cases check one note request per query at desktop and phone size, and that a stuck search ends in an error that can be retried.
