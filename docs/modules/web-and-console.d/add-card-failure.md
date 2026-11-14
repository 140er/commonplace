- **Owns:** `board.js` owns what the Add card form does when a create fails (`addFailureText`,
  the pending flag and the kept failure); `app.js` only redraws the board.
- **Promises:** A failed create leaves the form open with every typed word, and shows one
  plain line: "Could not add card: <the server's reason> (error 409). Your words are kept
  here. Try again." A failure with no reply says to check the connection. The line is drawn
  again when the board redraws on a poll, and is forgotten on a retry, a cancel or another
  board. Only one create is out at a time: a second tap, or a tap on a composer drawn
  while the first request is out, is ignored and the button stays disabled.
- **How it fails:** A request that never answers keeps the form locked until the browser
  gives up on it; the message then appears and the button is enabled again.
