- **Owns:**

  - Round 4 styling of the cloud workspaces section: touch sizes that follow the pointer (`@media (pointer: coarse), (max-width: 699px)` in `compute.css`), the `--control-line` token defined for Deck in `deck-capy.css`, dimmed `aria-disabled` buttons, an edge on Deck's danger button, one-line fixed-label discard buttons, a visible ring on programmatically focused headings and the summary, and full-height summary link targets.
- **Promises:**

  - A touch tablet gets the phone's 44px targets, 16px inputs and 14px text; labels, hints, errors and size captions are 14px on touch; an `aria-disabled` button never looks live; Deck's discard button has a visible edge and never wraps; Classic does not define or depend on a Deck-only colour.
- **How it fails:**

  - A rule that drifts is caught by `tests/test_compute_workspaces_browser.py`, which measures targets, font sizes, the disabled look, the danger edge and the token's home in a real browser at 390px, 650px, 768px (touch) and desktop width.
- **Tests:** `tests/test_compute_workspaces_browser.py`.
