### Phone: every destination reachable, card menus on screen

Decision: the phone bar keeps its four sections and gains a fifth tab, More, that opens
a sheet listing what the desktop rail and account menu offer (Board, Memory, Needs me,
Projects, Console, Rules, plus Admin and Instance console where allowed, Switch
workspace and Sign out). Nothing new is invented: the sheet reuses the rail's routes and
the Today waiting count, so a destination cannot be reachable on desktop but missing on
a phone. The sheet's open state lives in the shell, not in the markup, because the shell
redraws its chrome on every refresh; it closes when the route changes.

On Today the phone shows Reply as an inline button next to Hold, so answering never
depends on a menu. The card menu now only holds Turn down and is placed by script after
it opens (shifted sideways or flipped above its button) so it stays inside the screen.
Visual styling stays with the existing Deck tokens.
