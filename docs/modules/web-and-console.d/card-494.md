- **Owns:**

  - Deck card details and the personal queue use the existing surface, well and text tokens at both screen sizes. Queue filters follow the board's segmented pill styles; the drawer scrolls above its comment footer.

- **Tests:**

  - `tests/test_deck_card_needs_browser.py` measures every rendered text pair, including placeholders, in the real shell at phone and desktop sizes; checks empty and populated queues, filter styles and switching, phone gutters, horizontal overflow, and the Pocket field's bounds and hit target above the comment bar.
