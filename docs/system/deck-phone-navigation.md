### Deck navigation on phone project screens

- The shell and the shared chat dock both render phone navigation through the active Deck handle before falling back to Classic. The dock must never replace four Deck destinations with its Classic tabs after a board or Needs me render.
- Phone layout uses one viewport-height row and anchors the Deck tabs to its bottom edge. Project settings add a phone-only history entry in Deck so Close and browser Back return to the underlying screen. Settings exposes the workspace switch beside Projects, Memory and Needs me; the board title bar provides a project-settings action.
- The change is scoped to Deck phone navigation and layout. Classic and desktop retain their existing presentation.
