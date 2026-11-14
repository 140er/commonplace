- **Owns:**

  - `signin-recovery.css` styles the token-free recovery document for email links and throttled browser navigations.
- **Promises:**

  - The sign-in form displays Sending while disabling duplicate submission, an account-neutral sent notice with Send another link, and readable throttle or network failure notices with Retry. The email and destination stay in the form. Requests time out after 15 seconds so the form becomes usable again.
  - Expired and throttled sign-in callback screens retain the email form and a Retry action. Recovery document links contain no challenge token.
