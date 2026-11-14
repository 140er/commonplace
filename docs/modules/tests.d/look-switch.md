- **Owns:** `tests/deck_look_switch.mjs`, `tests/test_deck_look_switch.py` and
  `tests/test_deck_look_switch_browser.py` prove the Look switch placement, switching
  and the collapsed token controls; `tests/commonplace_deck.mjs`,
  `tests/deck_settings_capy.mjs` and the Settings browser tests follow the new
  positions and selectors.
- **Promises:** The browser-free harness fails on the old behaviour (switch at the
  bottom, canvases growing without bound, token buttons beside the toggles) and every
  rule has a mutation test. The browser file skips cleanly where no browser exists and
  measures that the page keeps running after each switch on the same route.
- **How it fails:** A moved selector or a changed order fails the harness by name
  rather than skipping.
- **Tests:** Run the three files above plus `tests/test_commonplace_deck.py` and
  `tests/test_deck_settings_capy.py`.
