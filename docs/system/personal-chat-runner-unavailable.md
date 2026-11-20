### Personal chat without an available runner

An enabled hosted front cannot answer a message that requires the person's own account. If that account has no eligible online runner and no fresh subscribed main session is active, the relay writes a private `no_runner` answer on the original message. It tells the person to start their runner with the selected provider enabled and send the message again. The refusal creates no hosted or relay job and does not change account selection or permissions.

The existing reply store admits one answer per board and message, so repeated delivery preserves the refusal's association. An unrelated machine cannot answer it. Eligible online routing and active-session precedence retain their existing rules, including the eligible online runner's always-relay preference.
