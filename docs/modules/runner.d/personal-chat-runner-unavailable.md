- **Promises:**

  - When personal chat has a valid account but no eligible online runner, the poster receives a private `no_runner` answer explaining how to start the runner with the selected provider and resend. The same rule covers a hybrid personal override. No hosted or relay job is created.

- **How it fails:**

  - A fresh, subscribed main session keeps precedence and receives no runner-unavailable answer. Existing online routing, account eligibility and the online runner's explicit always-relay preference remain in force. Repeated delivery keeps one refusal attached to the original message.

- **Tests:**

  - `tests/test_personal_chat_runner_unavailable.py` covers unavailable personal runners with an enabled hosted front, private replies and repeated delivery, successful online routing and active-session precedence.
