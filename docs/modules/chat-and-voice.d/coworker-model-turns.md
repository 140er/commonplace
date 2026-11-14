- **Owns:**

  - `chat/coworkers/model_effort.py` validates workspace model choices and stores immutable selection metadata beside coworker history. `turn_mode.py` journals explicit composer intents and routes them through the existing message/control operations. All tables are additive; existing conversation and receipt rows remain intact.

- **Promises:**

  - Creation: `POST /api/coworkers/conversations` accepts optional `model` (a configured model id string) and `reasoning_effort` (`low`, `medium`, `high`, `xhigh`, `max`). Null, empty, non-string and unknown values return 400. Example: `{"kind":"dm","bot_ids":["bot_example"],"model":"configured-model","reasoning_effort":"high"}`. The conversation returned by creation, detail, list and bootstrap includes `model` and `reasoning_effort`; clients can show `Runs on <model>` once in the header. An unconfigured model is returned as null.

  - Allowed models come from the current workspace/project catalog and settings, filtered to the existing chat front provider. Other workspaces, other projects and other providers are excluded. Choosing a model never creates a provider, endpoint, credential or consent. Omitted fields inherit the current configured default. Existing local-only, board consent, disabled-provider and key checks still precede calls; queued explicit choices are validated again before dispatch.

  - Unified composer: `POST /api/coworkers/conversations/:id/messages` keeps required `text` and `request_id`, optional `bot_id`, and adds `mode` (`steer` or `chat`, default `chat`), `fallback` (boolean, default false), and optional per-turn `model`/`reasoning_effort`. Example: `{"text":"Please examine this case","request_id":"turn-example","mode":"steer","fallback":true,"reasoning_effort":"high"}`. Overrides apply to that turn only; they never change the coworker's defaults or the configured task builder.

  - Chat returns 201 with the existing `message`/`job` result plus `mode`, `requested_mode`, `model` and `reasoning_effort`. Steer selects the conversation's uniquely addressed active canonical run and uses the existing `follow_up` control; it returns 200 with its existing `receipt` and the same mode/selection fields. With no active run it returns 409 `no_running_job` without saving a chat message. Only an explicit `fallback:true` permits Chat, whose response includes `"mode":"chat","requested_mode":"steer","reason":"no_running_job"`. Multiple active runs return 409 `ambiguous_running_job`; an unaddressed group returns 400 `bot_required`.

  - A steer receipt preserves the existing control outcome: today it saves an exact canonical task comment and reports `comment_saved_application_unsupported`, never claiming the worker consumed it. It returns `model_effort_applied:false` because the existing control protocol cannot change a live worker's model or effort. Selection metadata is still validated and audited. `POST /api/coworkers/jobs/:id/controls` remains the explicit job alias, including stop, and accepts the same optional model/effort fields.

  - Requests with no new composer fields preserve the legacy message response shape and idempotency. Composer retries pin the applied mode and target, never turning a retry into a different run or duplicating a control. Changed text, mode, fallback, target or overrides under the same retry key return 409 `request_conflict`. Unknown models return 400 `unknown_model`; bad efforts return 400 `invalid_reasoning_effort`; malformed mode/fallback/fields return 400 `invalid_request`.

  - Person sessions, same-origin JSON, membership, private conversation audiences, board/task authority, originating-session and runtime publication fences are unchanged. A readable shared run does not grant steering authority, and authority refusals cannot fall back to Chat. Existing tokens gain no tools or authority. Audit actions `coworker.create` and `coworker.turn` record approved model/effort and applied turn mode, with no conversation text or credentials.

  - Explicit effort is sent as `reasoning_effort` to the configured compatible transport and `output_config.effort` to the configured Anthropic transport. Omitted effort adds no payload field. The closed enum follows the [provider effort contract](https://platform.claude.com/docs/en/build-with-claude/effort); model-specific provider refusals remain bounded errors without retries or fallback providers.

  - Model settings and scoped catalogs are snapshotted under the outer control guard before taking a coworker writer lock. Views and runtime selection consume that snapshot without taking the control lock; selection audits publish after the private transaction closes. Dispatch/publication refresh policy before checking queued choices.

  - Steer checks all task-origin candidates against current canonical run state rather than a recent chat history window. Effort transport failures use the bounded model-error contract and never leak an HTTP store error into the provider layer.

- **Tests:**

  - `tests/test_coworker_model_turns.py` covers persistent creation/detail/list defaults, the validation and mode matrices, catalog isolation, per-turn runtime selection, non-sticky overrides, canonical control alias/idempotency and person/member/agent authority. Existing coworker API/store/runtime/control tests protect compatibility.
