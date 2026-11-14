- **Owns:** `deck-shell.js` and `deck-shell.css` own the Deck desktop rail,
  bot trees and phone chrome.
- **Promises:** Existing routes and four Deck destinations remain stable; state,
  unread and section metadata come only from readable hub records. Named sections
  fold; absent states stay hidden. Message composers carry the voice control.
  Bot avatars use one letter unless first letters collide; narrow composer
  hints stay short and truncate with an ellipsis when space runs out.
- **How it fails:** Directory failures clear affected lists and offer Retry;
  account changes discard stale responses. Classic navigation remains separate.
- **Tests:** `tests/test_deck_shell.py` covers routes, counts, trees and phone
  chrome; `tests/test_deck_shell_browser.py` checks real shell geometry and actions.
