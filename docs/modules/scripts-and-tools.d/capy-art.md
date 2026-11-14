- **Owns:**

  - `scripts/capy_art.py` Bayer-dithers the grayscale sprites in `design/capy-art/` with a 4x4 ordered threshold and writes `commonplace/web/static/deck-art-data.js`. Run `python3 scripts/capy_art.py` to regenerate and `--check` to confirm the committed file matches. It uses only the standard library, reads only 8-bit grayscale PNGs and is deterministic.
