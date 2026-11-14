- **How it fails:** A board request refused by a lock timeout answers 409 with code `busy`,
  a `Retry-After` of 2 seconds and the plain sentence "The workspace is busy with another
  change, so this did not go through. Try again in a moment." (card create and the board
  list use the same reply). Nothing was written, so a retry adds exactly one card.
