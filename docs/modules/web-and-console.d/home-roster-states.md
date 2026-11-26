- **Owns:** `deck-shell.js` owns the Home entry in the rail and in the phone More sheet;
  `deck-today-capy.js` renders the screen behind it.
- **Promises:** The rail lists Home before Today and marks it current on `#/` and on the old
  Today address; the phone bottom bar keeps its four tabs and More, with Home as the first
  entry in the More sheet. Without a registered Home screen neither the rail nor the More
  sheet offers Home.
- **How it fails:** A Home screen that is not registered leaves the entry out instead of
  linking to a page that cannot open.
