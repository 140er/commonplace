- **Owns:**

  - Cloud workspace rules added after review round 5 (`workspaces.py`, `routes.py`, `control.py`, mirrored in `compute-workspaces.js`): image and name validation edge cases, tolerant reading of a stored image on edit, and the error mapping of the older Compute routes.
- **Promises:**

  - A bare local suffix (`home.arpa/app`, `ts.net/app`) is `registry_private` like the host names under it; text after `@` that is not `algorithm:hex` (`pass@ubuntu`) is `user_info`, never a digest; a lone dotted repository (`my.image`, `node.js`, `my.app:1.0`) is accepted, while a website name with no path (`ghcr.io`, `ghcr.io:443`, a name ending in a common website ending) stays `registry_no_path`; U+FFFC is `characters` in a name.
  - A stored image reference that cannot be read never blocks an edit of something else: a rename or a limit change on that record succeeds, and choosing a new image repairs it.
  - A wrong-typed value in a body of an older Compute POST route (gateways, targets, scopes, proposals, scripts, credential binds, scope revoke, proposal approve and cancel) is `400 invalid_request`; a scope with no task number is the same refusal, not a database error.
- **How it fails:**

  - On the cloud workspace and overview routes a `TypeError` or `OverflowError` from the store is not turned into a refusal: it surfaces as the bug it is (the older routes map it to `invalid_request`).
- **Tests:** `tests/test_compute_workspaces.py` (unreadable stored image, shared fixture cases), `tests/test_compute_wrong_typed_bodies.py` (every older POST route with wrong-typed values, the mapping on the older routes and its absence on workspace routes) and `tests/compute_workspaces_ui.mjs` (the same fixture through the browser rule).
