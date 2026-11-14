### A failed Add card keeps the words and says why

- A usability test saw Add card fail on a phone with HTTP 409 and no message, and another tester saw 409 on the board list. Both were the lock-timeout refusal, which answered 409 with the bare word "busy"; the form's own line was drawn on a composer that the next poll replaced, so the message vanished with it.
- Decision: the refusal keeps status 409 and code `busy` (clients already branch on them) but carries a plain sentence and `Retry-After`. The form keeps the typed words, shows the server's reason with its number, and the message lives in module state so a redraw shows it again.
- A create is one at a time per page: the board redraws while a request is out, and a fresh enabled composer let a second tap add the card twice. The pending flag also disables the redrawn button.
- Not changed: the Who field, the Move menu, and the lock timeout itself.
