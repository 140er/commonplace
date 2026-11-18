- **Tests:**

  - `tests/budgets_page.mjs` (run by `tests/test_budgets_page_ui.py`) covers the page wording and amounts, status badges, the member view, explained empty, unknown and failed states with only a 401 as signed out, escaping, the form checks, the confirm before any write, the PUT and DELETE bodies, a refused write keeping the old budget, and the Needs me budget row; `tests/test_budgets_page_browser.py` checks the Settings link (Classic and Deck) and the page at desktop and phone widths, including a confirmed save, a failed write and the member view.
