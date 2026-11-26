- **Owns:** `app.js` owns when the card drawer draws again (`cardPanel`) and what Cancel does to
  an open Remove assignment form (`cancel-form`).
- **Promises:** An open Remove assignment form, in the Classic and the Deck look, is left in
  place while a read of the card returns the same card version the form was drawn from, so the
  form node and the reason typed in it survive polls and refreshes that arrive often. A new
  card version, a card that is closed, or another route still draws the drawer again. Cancel
  takes the form out first, so the drawer comes back as before.
- **Tests:** `tests/remove_assignment.mjs` (run by `tests/test_remove_assignment_ui.py`) draws
  the open card again with the same version, with a new version and with no form open, and
  presses Cancel; `tests/test_remove_assignment.py` covers the form in a browser.
- **How it fails:** If a card changed without its version changing, the open form keeps the old
  card text until it is submitted or cancelled; submitting still checks the version.
