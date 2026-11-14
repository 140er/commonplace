### Rules for reviewers and at session start

- `commonplace review-rules` hands a reviewer the effective rules of the token it is run with, and `--verify` checks that a verdict repeats the current rule version. That line shows which rule set the reviewer was given; it does not show that the reviewer applied each rule. Review tools outside this repository have to call both steps themselves.

- The session-start hook adds a notice when its context does not open with a complete rule set. It recognises the server's own first and last rule lines; a context that only quotes those lines as data still gets the notice.
