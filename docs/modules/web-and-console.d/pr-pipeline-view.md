- **Owns:**

  - The Pipeline screen at `#/pipeline` (`deck-pipeline.js`, `deck-pipeline.css`), a Deck-only read-only view of `GET /api/pipeline/prs`: one card per pull request with connected blocks (card, builder started, PR opened, gate, check, merge, the four deploy steps, backup proof), each with its state in words, start, end and duration in Amsterdam time, links, and the wait between blocks drawn on the connector; a header with the lead time, the slowest step, the longest wait and where the PR is now. It is listed in the Deck account menu and the phone More sheet, offers a fixed example (`?demo=1` or the button) and an empty state that says where data comes from. Blocks wrap on desktop and stack on the phone; reduced motion stops every animation. The empty-state art is the `pipeline` sprite of the Capy art.

- **Promises:**

  - Only https references become links; every text value is escaped; nothing is loaded from another origin. The screen reads again every 15 seconds while shown, keeps each pull request's open or closed choice and the scroll position, and shows a plain sentence for a failed or refused read.
