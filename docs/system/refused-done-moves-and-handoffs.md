### Refused Done moves and handoffs

When the service refuses `PATCH /api/cards/<n>` with `status: "done"` or
`POST /api/cards/<n>/handoff` with 403, the answer is
`{error, next_step, code}`: `error` is a plain reason, `next_step` says what to do
next (for example release the restricted-origin card first, or move a todo card to
Doing before Done), and `code` is `done_refused` or `handoff_refused`. Refusals the
service does not know get its own text plus a generic reload-and-ask step. Only the
wording is added here: who may move or hand off a card, and every status code
(404 not found, 409 version clash, 400 invalid input), stay as the service decides.
The owner web client joins `error` and `next_step`, and shows a readable message
for a 403 that carries no JSON body.
