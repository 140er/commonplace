# Upgrade notes

Workspace owners can invite people from Admin. An invite link works once, expires
(seven days by default) and can be revoked; the person joins as a member. Nothing
changes for existing workspaces until an owner makes an invite. Invites do not create
accounts, so `COMMONPLACE_SIGNUP` still decides whether a new person can sign up. See
`docs/invites.md`.

New account signup now defaults to closed for both email and social sign-in.
Operators who want to accept new accounts must explicitly set
`COMMONPLACE_SIGNUP=open`. Existing users can still sign in while signup is
closed; missing or invalid settings do not enable account creation.

Email links open a confirmation page. Signing in requires an explicit button
press; opening or previewing the link does not consume it.
