### Card review decision announcement test follows the product wording

- The decision status line for card review comments says "The bot stops answering these comments" since the Deck rebuild renamed the reviewer from Coworker to bot. `tests/test_card_review_decision_browser.py` waits for that wording like `tests/test_card_reviews_browser.py`, so a changed announcement fails all twelve cases instead of passing unnoticed.
