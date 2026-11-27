### Admin route: members see who can open it

A member who opens `#/admin` directly sees "Only the workspace owner can open Admin. Ask the
workspace owner if you need something changed there." and Admin sends no requests. The direct-route
harness checks that copy rather than the server's refusal text.
