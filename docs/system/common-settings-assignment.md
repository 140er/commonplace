### Common settings and reservation removal

Common Settings lead with notifications, look, connections and workspace navigation.
Appearance has a direct shortcut alongside those destinations. Advanced contains
the existing grouped, searchable action-permission catalog and starts collapsed.
The existing permission controller, stored modes and server checks are unchanged.
An explicit shortcut opens Advanced; repeated presentation passes keep its state
and move live nodes without replacing forms.

Reserved, not-started cards show who holds them (Reserved for, by display name) on
the board card and in the card header, and expose Drop reservation outside the Deck
Edit card disclosure. The confirmation says the card goes back to the pool and
explains who can pick it up; the reason is optional (a blank one is saved as
Reservation dropped) and remains in the activity. Hand off stays available for
choosing a recipient. Dropping still posts to the versioned
/api/cards/<number>/handoff route with to=anyone. A connection that does not hold
the reservation cannot remove it; the workspace owner can. Active work, origin
release, proposal and stale-version guards remain enforced by the same path.

The regression harness fails on the pinned base for common section order,
Advanced containment and directly visible removal. Browser cases at 1280 and 390
check those destinations, retained search, overflow and real removal writes.
Existing removal tests cover the optional reason, refusal messages and unchanged
audit state on refusal.
