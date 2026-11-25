- **Owns:**

  - The chat front's choice between the hosted front and the person's own runner when the model mode asks for a person's own account.

- **Promises:**

  - In personal mode (or a hybrid override) the hosted front's operator key is never used for that person's message; it goes to their runner, or they are told in the chat why not.

- **How it fails:**

  - An unreadable mode skips the hosted front and answers `mode_unavailable` in the chat.

- **Tests:**

  - `tests/test_model_modes.py`.
