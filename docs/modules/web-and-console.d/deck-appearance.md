- **Owns:** Capy appearance tokens, stage renderer and settings.

  The opt-in Deck look loads its Capy colour and shape tokens from `deck-capy.css`.
  `deck-dither.js` contains the dependency-free `StageDither` canvas renderer: three
  procedural scenes, ordered Bayer 4×4/8×8 thresholds, the reference value pass and
  four colour ramps. One texel represents one dot. ResizeObserver repaints the
  stage; only the sparse optional twinkle uses a timer. Reduce Motion stops it,
  including dots already lifted, and disposal releases its observer and timer.

  `deck-appearance.js` mounts the inset stage behind the shell and the Appearance
  panel in Settings. Home (`#/` and `#/home`) is undimmed; other routes apply a
  0.45 renderer dim and a separate 50% stage veil. Home routing belongs to the
  Home screen. The Settings wrapper preserves existing section content and order,
  placing Appearance beside it on desktop and last on narrow screens.

  Scene, Bayer pattern, vignette, dot size and surface opacity are validated and
  saved under a localStorage key containing both workspace and person. The initial
  render uses the saved values. There is no server schema or external service.
  The classic look does not mount the stage, panel, or appearance tokens.

  Verification: `test_deck_appearance.py` wraps fixed-input Node checks for value,
  hue, thresholds, persistence isolation, and reduced-motion cleanup.
  `test_deck_appearance_browser.py` optionally checks crisp rendering, both dim
  layers, Settings interaction/reload, resize, dark-dot distribution and the render
  budget. It skips when browser support is unavailable or explicitly disabled.
