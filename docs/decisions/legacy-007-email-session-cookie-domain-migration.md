### Email session cookie domain migration

- When a shared session cookie domain is configured, successful email verification first expires the same-named host-only cookie at Path=/, then sets the new domain cookie.

- The redirect destination and session security attributes stay the same.

- This prevents an older host-only session from taking precedence in the browser's Cookie header.

- Invalid or used email links still return a visible error without changing cookies; requesting a link returns the same confirmation for every account.

