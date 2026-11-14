- **Owns:** `tests/deck_shell.mjs`, `tests/test_deck_shell.py` and
  `tests/test_deck_shell_browser.py` exercise Deck shell navigation.
- **Promises:** Browser-free cases enumerate stable routes and test action counts,
  optional metadata, named sections, identity isolation and composer chrome.
- **How it fails:** Node assertions report the failing contract; optional browser
  checks skip when disabled or when the browser dependency is absent.
- **Tests:** The shell wrapper plus related rail, app and navigation wrappers;
  optional browser checks capture desktop, tablet and phone at scale two.
  `tests/test_deck_phone_navigation_browser.py` measures attached navigation
  targets atomically so a replaced board cannot yield empty computed gutters.
  Rail browser cases also cover workspace creation access and the Coworker
  panels with composer-aware phone chrome after integration.
  The workspace creation browser check expects the shared Capy surface colour
  after the Appearance and shell integrations.
  Workspace dialog shape and assignment-removal visual pins use the Capy
  dialog, raised surface, ink and border tokens; authority assertions remain.
  Home/Today integration verifies the registered root and Today loaders, palette
  hooks, live queue counts, rail links and composer-aware microphone placement.
  Board integration checks the rail alias, unchanged classic routing and the
  divided Board's phone inset alongside existing drawer navigation.
  Shell avatar regressions cover unique letters, collisions and roster order;
  `tests/commonplace_deck_today.mjs` checks short composer copy and truncation.
