- **Owns:** `deck-shell.js` owns the phone More sheet (`renderShellMore`, the More tab in
  `renderShellTabs`, its open state kept across shell refreshes); `deck-home-today.js`
  owns `placeMenu`, which keeps an opened Today card menu inside the viewport.
- **Promises:** On a phone every destination the rail and account menu offer (Board,
  Memory, Needs me with its waiting count, Projects, Console, Rules, Admin and Instance
  console for those who may, Switch workspace, Sign out) is two taps away: the More tab,
  then the entry. The sheet uses the rail's own routes, stays open while the shell
  refreshes, and closes on any tap inside it, on Escape, or when the route changes.
  Today cards show Reply as an inline button on a phone; the card menu appears only when
  Turn down is offered and is shifted or flipped so it never leaves the screen.
- **How it fails:** If the page script cannot measure the menu, the menu keeps its
  stylesheet position (Reply stays inline, so no action depends on it). The desktop
  rail and card row are unchanged.
