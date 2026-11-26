- **Owns:**

  - `commonplace/today_email/`: the opt-in email for new Today items. `POST /api/today-email` with `{"enabled": true|false}` saves a person's choice and `GET /api/today-email` reads it back with `available` (a mail server is configured) and the address. The mail goes through `commonplace/mailer.py` and its `COMMONPLACE_SMTP_*` settings; no other sender exists.
  - The phone notification copy in Settings: an owner never reads an instruction to ask the owner. Without a notification service the owner sees that phone push is not available on this server; a member also sees who to ask.

- **Promises:**

  - Off by default and per person. Turning it on records what Today already holds, so only items that arrive later send. An item that leaves Today and comes back is new again.
  - What a person is mailed is what their own Today list holds, read through `needs_me`, so nobody hears about an item they cannot read. The mail has the card number, its title and links, never the body, notes or context.
  - A burst arrives as one mail listing up to ten items, at most three mails per person in any hour; items over the cap stay waiting and go out in a later mail. A failed send counts against the cap and is retried.
  - Without a mail server the setting says "Email isn't set up on this server", saves nothing and no thread runs. The mail names Settings, Email for Today, as the way to turn it off.

- **How it fails:**

  - `tests/test_commonplace_today_email.py` fails if the setting turns on by default, a mail carries card text, an opted-out person or a member who cannot read the item is mailed, one item sends twice, or the hourly cap is exceeded.
  - The `tests/commonplace_signin_render.mjs` copy cases fail if an owner is told to ask the owner.

- **Tests:** tests/test_commonplace_today_email.py, tests/commonplace_signin_render.mjs, tests/deck_settings.mjs and tests/test_settings_load.py.
