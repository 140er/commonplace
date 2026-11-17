### Human names in connection setup and conversation invitations

Conversation creation now presents searchable workspace members, a project select
and the exact audience before submission. The existing member_ids and board API
fields continue to carry IDs and a slug. Direct messages keep their one-person,
one-bot boundary; group creation still checks every member against current server
authority. The directory adds names only for people in the signed-in workspace,
using the same stored-name/email-local-part fallback as the app's people labels.

Connections store a separate display_name, while name remains the identifier used
by policy, parent links and connection actors. The browser derives a lowercase
identifier with spaces replaced by hyphens and displays it while typing. The API
also accepts ordinary names containing spaces, while legacy identifier requests
keep their spelling. Explicit display_name requests must supply its matching
identifier. Existing rows display their unchanged identifier as a fallback.
Identifiers remain unique within a workspace, including revoked connections.

Before: “Other workspace user IDs”, “Project slug (optional)”, and “Invalid or
duplicate agent name, client or parent”. After: “Workspace members”, “Search
members”, “Project (optional)”, “Audience: you …”, and “Identifier: research-helper”
for the display name “Research Helper”. Invalid names explain the allowed
characters and starting character; duplicates ask for another name; client and
parent errors identify their cause. Failed creation preserves entered values.

The schema addition has an empty default and changes no existing identifier,
token hash, lifetime, scope, authorization or parent relation. Token reveals stay
inside their existing one-time surface. Chromium evidence uses synthetic local
accounts and captures forms before credentials are revealed.

The admin connection inventory explicitly opts out of the display-name field in
the shared serializer. Its SQL projection and response fields remain unchanged;
Settings and connection setup continue to include the stored display name. This
keeps a new Settings field from breaking a narrower administrative read.
