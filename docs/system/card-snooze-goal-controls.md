### Card return dates and goals in the card panel

The Deck card panel and classic drawer share Snooze, Wake now and Goal controls. They call the existing card-snooze and card-goal routes; no separate storage, timer or dispatch mechanism is introduced. The app follows the routes' signed-in workspace-owner authority. Assigned agents retain their existing snooze-tool authority; goal assignment remains owner-only.

The browser's IANA timezone names every displayed return time. Tomorrow and next Monday mean 09:00 on the next calendar day and strictly following Monday respectively. Later today means three elapsed hours from now, capped at 23:59 on the same local date; during the final minute it asks for tomorrow or a picked time instead of sending a past instant. Picked times remain local wall times: the shared time parser rejects missing spring-forward hours and chooses the first occurrence of a repeated autumn hour.

A snoozed card remains on the board with a plain return-time line. The decision queue uses the same active-snooze predicate as dispatch, so a snoozed proposal is not offered as a decision. Waking restores eligibility without approving the proposal or starting work.

The goal selector assigns or clears an existing goal. It retains the current archived goal while excluding other archived goals. Listing goals never delays the card read, and a failed list keeps editing disabled. Literal goal text is escaped and inserted after the Deck's display-copy transformation.
