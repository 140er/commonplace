### Identity failures are connection failures until HTTP 401

The identity bootstrap previously caught every failure of `GET /api/me` and
rendered `Sign in | Email a sign-in link`. A timeout, aborted request, network
failure or HTTP 500 therefore looked like an invalid session, although the cookie
could still be valid. The shell could also claim `No bots yet` before its
personal roster and coworker directory had answered.

Only HTTP 401 now enters sign-in. Explicit sign-out retains its existing behavior.
Other identity failures show `Could not reach Commonplace, retrying...` and a
Retry button, without mounting an empty authenticated shell or changing the
cookie. An absent identity response also remains unknown. The initial attempt
has a ten-second complete-read deadline. Three further attempts each have a
three-second deadline, following one, two and four seconds of backoff. Boot runs
after the static module graph has evaluated; each request creates its own timer
when invoked, so module loading does not consume the first request's budget.
Transport and body decoding remain inside the existing bounded-request helper.

Retry skips the pending backoff without adding another request. During an
in-flight request it coalesces with that attempt. After automatic exhaustion the
message becomes `Could not reach Commonplace. Retry to connect.` and Retry starts
a new bounded sequence. A successful identity read mounts the shell and resumes
the original route. HTTP 401 stops retrying and preserves callback sign-in text.

The sign-in call-site audit found only identity failure, the HTTP-401-driven
`session-expired` event, and explicit logout. The latter two stay unchanged.
Provider-discovery fallback runs only inside sign-in, after a refusal or logout.
Voice initialization handles its own failure. Main shell refresh and Settings
already retain data or show an error. The earlier unhandled project-discovery
read for classic home, chat and shared-card entry now shows `Could not load
projects.` with a Retry button that repeats route discovery. Event polling stays
paused until route discovery succeeds, so it cannot replace this failure with a
screen selected from an empty project list.

The rail distinguishes pending or unavailable discovery from a successful empty
list. `No bots yet` requires both a successful personal roster and coworker bot
directory. A transient directory refresh retains known rows and shows its retry
message. HTTP 401/403/404 clears the affected directory rows rather than retaining
revoked access. Viewer changes retain the existing reset and stale-reply guards.
No search, authorization, stored-data or ordinary-read timeout contract changes.

Disposable Node fixtures execute the shipped bootstrap, API helper and rail with
a virtual clock. On pinned develop `9c4d8340`, eight identity scenarios fail while
HTTP 401 passes; three rail scenarios fail while known-empty discovery passes.
The regression set covers timeout, abort, network, HTTP 500/503/403, null identity,
five-second cold success, bounded retry exhaustion, manual recovery, project
entry failure, pending/error/empty rails, stale rows and access refusal. Existing
sign-in and card 591 cold-start cases run alongside them. Live repro scripts were
read for context only; no production session or endpoint was accessed. Module
waterfall changes are outside this hot-fix.
