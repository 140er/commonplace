## tool-policy

- **Paths:** `commonplace/tool_policy/`, `commonplace/web/static/tool-policy.js`, `tests/test_commonplace_tool_policy.py`, `tests/tool_policy_view.mjs`
- **Owns:**

  - The per-workspace tool policy: the owner sets each tool action (every MemPalace tool the gateway serves and every gateway tool, by name) to Allowed, Ask first or Off, in the control database (`tool_policy`), through `/api/tool-policy` and the Tool policy section of Settings.

  - The check of one tool call (`gate.check_call`), called once from the gateway's dispatch point after the connection's own policy, and the same check for a connection's REST form of an action (`gate.rest_refusal`).

  - Ask-first requests (`tool_approvals`): one row per request, bound to the workspace, the connection id, the action and a SHA-256 digest of the canonical arguments, with the review card that asks the owner.

- **Promises:**

  - Allowed is the default and stores nothing: a workspace with no rows behaves exactly as before, and `tools/list` never depends on the policy.

  - The policy only narrows. It runs after profile, scope, trust and owner gates, so a tool a connection may not call stays an unknown tool whatever the owner set, and no request is filed for it.

  - Off refuses the call with a message that names the action and the policy; nothing runs and the refusal is audited (`tool policy: off`).

  - Ask first never runs the call it holds. It files one question to the owner through the ordinary ask path (a review card with Approve and Deny as decision blocks, addressed to the owner, shown in Needs me). The same call runs once after a signed-in workspace owner picked Approve on that card. Another connection, another action or other arguments have another binding; the request closes before the call runs, so a second identical call asks again; the owner has 24 hours to answer and the approval is good for one hour; any other answer is a denial; an answer or a change on the card by anyone who is not a signed-in owner (the asking agent, another agent, a connection holding `cards:decide`, a member, a former owner) voids the request.

  - Only a signed-in workspace owner reads or changes the policy (403 for members and for every agent token whatever its scopes, 401 for anonymous callers); there is no MCP tool for it. Every change is audited (`tool_policy.set`).

  - It covers agent connections. People in the app are not gated, and neither are the gateway's own nested calls: each action is gated under its own name (`memory_save` set to Allowed still saves while `mempalace_add_drawer` is Off).

- **How it fails:**

  - Closed. A stored mode this version does not know counts as Off; a request that cannot be filed (the connection cannot ask on any board, or already has 20 open requests) refuses the call; an error while reading the policy or the request card fails the call.

  - Over REST, Ask first is refused (403 `tool_policy`) with a pointer to the MCP tool, because an approval binds to an MCP call's arguments.

  - An approval is spent when the call is dispatched, also when the call itself then fails on its own checks.

- **Tests:**

  - `tests/test_commonplace_tool_policy.py` drives real MCP and REST requests: unchanged defaults, Off on a gateway tool and on a memory tool, no widening, each Ask-first step, the binding (replay, other arguments, other connection, other action, other workspace), denial, both expiries, void requests, agents unable to approve, owner-only changes, invalid changes, audit rows.

  - `tests/tool_policy_view.mjs` (run by the same file) covers row rendering, escaping, ordering, filtering and the save request of the Settings section.
