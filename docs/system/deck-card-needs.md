### Deck card and personal queue surfaces

- The shared drawer and personal queue retain their classic layout and styling unless the document has `data-ui="deck"`. Deck overrides use existing tokens and the board's segmented pill style, with no new palette values.
- Card details scroll independently above the comment footer on a phone. Inputs use the well token and 12px corners; the personal queue uses the surface token and 20px corners with 16px phone gutters.
- Browser checks cover phone and desktop contrast, placeholders, empty and populated queues, filter switching and style parity, overflow and Pocket field visibility. Classic before/after captures are compared byte for byte by the external capture fixture.
