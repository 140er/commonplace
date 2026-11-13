- **Owns:**

  - The shared chat dock preserves the selected look when it refreshes phone tabs. Deck keeps Today, Bots, Compute and Settings on boards, cards, project settings, Needs me and Memory. Classic retains its two tabs.

- **Promises:**

  - Deck phone tabs stay at the viewport bottom, with content scrolling above them. A card or project-settings drawer can close before choosing a destination, keeping the journey within two taps. Browser Back closes either drawer; project settings restore the underlying screen without navigating away. The board's phone title bar exposes project settings. Settings lists Projects, Memory, Needs me and Switch workspace. Desktop styling remains unchanged.

- **Tests:**

  - `tests/test_commonplace_deck.py` executes the dock's tab redraw for each affected screen in both looks. `tests/test_deck_phone_navigation_browser.py` follows the real routes and checks drawer Back, destination links, bottom alignment, page width and workspace discovery.
