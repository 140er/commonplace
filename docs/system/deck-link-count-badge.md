### Deck link count badge

Decision: under the Deck presentation a device with more than three link ends shows its count, instead of omitting bundles as the first Deck build did.

The Deck map draws every link as its own stroke and has no end marks, so the classic collapsed-end badge (worst state plus count, placed clear of the disc) does not apply. Without any sign, a device with many overlapping strokes looked like a device with fewer links, so users could not tell links were stacked. The shared scene now counts link ends for the presentation too and hands the node a plain count with no state and no position; the Deck card draws it as a small chip straddling its top-right corner, clear of the name, role and status mark. The count keeps an 11px screen size because the card is already scaled by the inverse of the zoom. Selecting a link leaves the count whole because all strokes stay drawn. Three links or fewer stay unmarked, and a map without link targets draws no count. The classic look is unchanged.

Rejected: reusing the classic position search (it clears a disc and label plates the Deck card does not have), and a state-coloured badge (the card already carries the node state).
