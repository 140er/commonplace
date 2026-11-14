- **Promises:**

  - The invite confirmation uses the same-origin `join.js` module while retaining no-referrer and the shared CSP, including `form-action 'self'`. No new route is registered. Refused confirmations render the existing readable invite page; a network failure re-enables Join and offers retry. Browsers with scripting disabled are told to enable it and reopen the invite.
