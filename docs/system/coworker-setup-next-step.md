### Coworker setup always names a usable next step

Personal bots and conversation bots keep their existing storage, access checks and
routes. Creation shares one field component and one validation function: Name and
How to work use the same wording; the personal form also asks for its existing Job,
and the conversation form retains its optional connection link. Both headings and
submit controls say Create bot. Names keep spaces, case and non-ASCII text. The
shared validator matches each store: personal names allow 1–60 Unicode characters
and conversation names allow 1–80, excluding blank or invalid UTF-8 text. These are
display names with server-generated bot IDs, so connection identifier character
rules do not apply. Native maxlength is omitted because it counts UTF-16 units;
input and submit validation count Unicode code points, matching the server. The
previous conversation input allowed 100 while its server allowed 80; it now uses
the actual server bound. Connection identifiers and their validation are unchanged.

A conversation with no bots offers Create bot beside its DM prerequisite. It opens
the same editor as the directory action. Members who cannot create bots are told
who can. Empty project rosters say No bots on this project yet and link Add a bot
to the conversation directory, where creation and Message are available.

Configuration recovery follows the recorded outcome code. Missing models name
Project settings and Chat answers; with no project, the action opens the project
list and also offers Workspace settings for a local model. Missing keys name the
key fix there. Missing runners name Add machine and use the existing enrollment
entry in Admin. Failed responses show a reason and offer Try again in the
conversation, which focuses the composer without sending a duplicate request.
An unconfirmed response explicitly asks the person to check before sending again.
The project settings action prevents the anchor's default navigation before its
asynchronous import, so its panel remains open on the conversation screen.
Unknown model codes retain the existing escaped setup text and the owner settings
link when no specific recovery is available; members retain their owner guidance.

The browser captures compare production controllers from develop and the changed
head at 1280 and 390. They use synthetic roster and job data. A separate durable
browser journey checks real gateway persistence and replies from a synthetic
provider; no external model availability is asserted.
