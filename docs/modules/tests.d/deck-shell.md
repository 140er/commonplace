- **Owns:** `tests/deck_shell.mjs`, `tests/test_deck_shell.py` and
  `tests/test_deck_shell_browser.py` exercise Deck shell navigation.
- **Promises:** Browser-free cases enumerate stable routes and test action counts,
  optional metadata, named sections, identity isolation and composer chrome.
- **How it fails:** Node assertions report the failing contract; optional browser
  checks skip when disabled or when the browser dependency is absent.
- **Tests:** The shell wrapper plus related rail, app and navigation wrappers;
  optional browser checks capture desktop, tablet and phone at scale two.
