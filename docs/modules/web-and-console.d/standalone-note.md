- **Owns:** The Add a note action in memory.js, its composer in memory-note.js and
  shared styles in map.css, for Deck and classic Memory on desktop and phone.
- **Promises:** A note has exact-word preview, wing and room choices, Save, and a plain
  saved line linking to its drawer. It uses POST /api/memory, retains a failed draft
  for explicit retry, and fences late answers after leaving Memory. Standalone wings
  remain searchable without a project, and drawer links survive a fresh page load.
  Save results receive focus and scroll into view, including on a phone.
  Opening fetches cheap destinations only. Selecting a wing loads optional rooms
  with a 1.2-second client timeout and ignores stale or stopped-controller answers.
- **How it fails:** Plain empty, length, access, storage-low and busy messages keep the
  text visible. Saving locks duplicate submissions. Writes never retry automatically.
  Loading failures say The places to save could not be loaded. Try again. and offer
  Try again; save failures describe saving and retain the draft.
- **Tests:** tests/memory_note.mjs and tests/test_memory_note_browser.py exercise route
  use, verbatim preview, failed-save retention, search and drawer links in both looks.
