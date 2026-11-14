### The release gate follows the app's current interface

- The fresh-user journey had not run against the redesigned app, so five checks failed and two were blocked on the first run after the redesign: the interface now says project instead of space, the Memory page searches by "Search memory", and Settings and the card panel changed their markup.

- Decision: keep the journey on public routes and visible names, and guard those names with a browser-free test, rather than restoring old markup or adding test-only hooks to the app.

- Agent-created cards are inactive until the owner releases them, so the journey performs that release in the browser before the agent claims the card.

- Agent notes are written to the inbox wing and the Memory page lists only project wings. The journey records this instead of hiding it; whether the page should list the inbox is a product decision outside this change.
