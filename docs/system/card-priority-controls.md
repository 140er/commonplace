### Priority controls on cards

Every board card shows its level and whether it is unranked, chosen by a person, or chosen by the Coworker. Hovering or focusing the label reads the recorded attribution and Coworker reason; opening the card shows them too. The priority filter includes all four levels and a separate Unranked choice. Normal includes both unranked and explicitly chosen normal cards.

The workspace owner can open the priority menu from the card's priority label or its actions. Choosing Normal on an unranked or Coworker-ranked card makes it the owner's explicit choice, even when the level is unchanged. Members can read priority and provenance but cannot set it. Failed writes keep the old priority and explain the failure.

After a successful change, Undo restores the previous level as the owner's explicit choice; it does not restore default or Coworker provenance. The original attribution remains in card history. Undo uses exactly the successful change's returned version. An intervening edit makes it fail visibly without overwriting the newer card, and the client never retries with a fresh version.
