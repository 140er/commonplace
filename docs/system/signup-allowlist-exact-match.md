### Closed sign-up can name a few people, by exact verified email

This belongs to the Decisions section.

- Decision: a closed instance may set `COMMONPLACE_SIGNUP_ALLOW_EMAILS` so named people can sign up while everyone else keeps the existing refusal. It exists so sign-in with a real provider can be tried on a closed instance without opening it.

- The match is exact after strip and casefold, against emails the provider verified. Plus-addresses, domain wildcards and partial entries are deliberately not supported, because each would let an address the owner never named create an account.

- The list is capped at 20 and is never logged or echoed; a matched sign-up logs only `allowlisted signup`. The account cap still applies, and the setting is read at sign-in time, so changing it needs no code change.
