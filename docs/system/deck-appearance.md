### Deck artwork and person-scoped Appearance

Deck uses shared Capy tokens and a local procedural Bayer-dithered stage. Home is undimmed. Other routes retain renderer dim 0.45 and an explicitly uppermost 50% veil, with canvas opacity calibrated against the brightest exposed artwork dot in the reviewed Today reference. Phone artwork is full bleed; Appearance miniatures use the real renderer at a fixed 2px dot and remain independent of the page dot control. The default vignette is 60%.

Appearance choices load before the first stage paint and persist in localStorage under both workspace and person. There is no server table or external service. Existing Settings sections retain content/order, and the classic look remains unaffected. Resize and preference changes repaint; Reduce Motion stops the optional sparse twinkle and teardown releases observers/timers.

Reference pixel comparisons and composited-brightness checks accompany the screenshots. The 25ms renderer target passed a dedicated check, but concurrent-load captures exceeded it; raw measurements retain that performance caveat rather than claiming a universal wall-time bound.
