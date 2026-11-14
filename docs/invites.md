# Workspace invites

An owner lets one more person into a workspace with an invite link. The person joins
as a **member** and gets what members already have, nothing more.

## What an invite is

- A secret, shown **once** to the owner who made it. The server stores only its
  SHA-256; no later request, list, log or audit entry carries the secret.
- **Single use.** The first person who confirms the join uses it up. Two people racing
  for one invite admit exactly one.
- **Expiring.** Seven days unless the owner picks another whole number from 1 to 30.
- **Revocable** until it is used. Revoking twice is harmless; a used invite cannot be
  revoked (the person is already a member).
- Optionally **bound to an email address**: then only the account with that address can
  use it.
- It stops working when the owner who made it is no longer an owner of that workspace.
- A workspace holds at most 50 open invites.

An invite never creates an account, and Commonplace sends no invitation email: the
owner copies the link and sends it. The invited person signs in the usual way (so the
instance's sign-up setting still decides whether a brand-new account can be made) and
then confirms the join.

## Owner: make, list, revoke

On **Admin**, the **Invite people** block makes a link, shows it once with a Copy
button, lists the workspace's invites with their state (open, used, revoked, expired)
and revokes open ones. Leaving Admin, signing out or pressing Done clears the link.

The routes are for a **signed-in owner of the active workspace** only. A member, an
owner's token and an owner's OAuth connection all get 403; signed out gets 401.

| Request | Answer |
| --- | --- |
| `GET /api/invites` | 200 `{"invites": [...]}`, newest first, at most 200 |
| `POST /api/invites` `{"email"?, "days"?}` | 201 `{"invite": {...}, "secret": "inv_…", "link": "…/app/join?t=inv_…"}` |
| `POST /api/invites/<id>/revoke` | 200 `{"invite": {...}}`; 404 for an unknown id or another workspace's invite; 409 once used |

An invite in a list has `id`, `email` (empty when unbound), `state`
(`pending`, `accepted`, `revoked`, `expired`), `created_by`, `created_at`, `expires_at`,
`revoked_at`, `accepted_at`, `accepted_by` and `accepted_by_email`. Other fields in a
`POST` body, a bad email or `days` outside 1 to 30 answer 400 and make nothing.

## The invited person: join

1. Opening `/app/join?t=<secret>` moves the secret into a one-hour HttpOnly cookie
   scoped to `/app/join` and redirects to `/app/join`, so the secret does not stay in
   the address bar or history and is never part of the sign-in state.
2. A signed-out visitor is sent to the normal sign-in and comes back to `/app/join`.
3. The page names the workspace and the signed-in account and asks for one click on
   **Join workspace**. Opening or previewing the link joins nobody.
4. Confirming adds the membership, uses up the invite, ends the person's previous
   session and starts a new one in the joined workspace.

Refusals: an unknown, used, revoked or expired invite all show the same "This invite
does not work" page (404); an email-bound invite opened by another account says so
(403) and stays usable by the right account; someone who is already in the workspace
is told so and the invite is not used up. An agent token cannot join, and a confirming
request from another origin is refused. `/app/join` shares the per-address sign-in rate limit
with the other credential routes.

## Moving between workspaces

A person in more than one workspace picks **Switch workspace** in the command palette.
`GET /api/workspaces` lists only the caller's own memberships
(`id`, `name`, `role`, `personal`, `current`), their own workspace first.
`POST /api/workspaces/switch` `{"workspace": "<id>"}` replaces the session with one in
that workspace. A workspace the caller is not in answers 404 `Workspace not found`,
the same as one that does not exist. Both routes are for a signed-in person; tokens
get 403. Signing in again still lands in the person's own workspace first.

## What a member can reach

Joining grants the `member` role and nothing else: no connection, no token, no scope.
A member reads and writes shared memory and boards in that workspace. A member cannot:

- read any **private wing** (`private`, `private_*`), including notes they wrote there
  themselves. This is today's rule, kept as it is. Whether private wings stay readable
  by the owner only is an open product decision; invites do not settle it;
- see or reach another workspace: its name, people, connections, invites and memory;
- open Admin, list or manage connections, runners, task types or invites, or change
  the capture setting;
- find a thread they may not read in a listing, or read it by its id (it answers
  exactly like a thread that does not exist).

`tests/test_commonplace_invite_canary.py` proves these from the second person's side:
after a real invite and join it calls every memory tool and every `GET` route with the
member's own session and requires that no canary filed in a private wing, a private
chat thread or another workspace comes back or changes.

## Not included

No invitation email, no account creation by invite, no removing a member or changing a
role from the app, and no owner invites (an invite always makes a member).

## Create another workspace

Choose **Switch workspace**, then **Create workspace**. Enter its name and continue. The confirmation explains that this creates an empty isolated workspace owned by you; your current workspace is unaffected and none of its members or data is copied. Cancel creates nothing. After creation, choose **Open workspace** to switch or **Stay here** to keep working where you are.

Creation is available to signed-in people when the instance allows signups. A closed instance refuses new workspace creation with a plain reason. Existing account plans are preserved through the signup plan hook; a plan refusal leaves no workspace behind. There is currently no plan-based workspace count cap. Each person may create five workspaces in a rolling hour; a limit refusal explains when to retry. A lost response can be retried without duplicating the workspace.

`POST /api/workspaces` accepts only `name` and a UUID `request_id`. It requires the person's browser session and an explicit same-origin Origin; app and other bearer tokens cannot create workspaces. The request id belongs to that person. Repeating it with the same name returns the original workspace; another name is a conflict. Creation returns the new workspace id, name and owner role without changing the session. The existing `/api/workspaces/switch` route performs the separate switch.
