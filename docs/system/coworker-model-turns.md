### Coworker composer model choices and turn modes

- Coworker creation and message turns accept model and effort choices within the workspace's existing configured provider. Omission inherits the configured default; choices never enable a provider, grant tools, bypass project consent or change task builder settings.
- The conversation message route accepts explicit Chat or Steer. Steer reuses existing canonical controls and their conservative receipts; fallback to Chat requires an explicit boolean. Immutable intent/selection journals preserve retry mode and scope; person, private-audience and canonical-control authority remain unchanged.
- The complete request/response, enum and error contract lives in the chat-and-voice module's coworker model turns fragment.
- Conversation list responses retain directory section, unread, state, audience and ownership fields alongside model/effort using the existing bounded query; detail may also include live ownership. Creation and stored conversation reads retain the persisted fields.
- Model-policy snapshots precede coworker writer locks; pure view/selection helpers use the snapshot, and audit writes follow private transaction closure. Canonical active-run lookup is independent of recent chat history length.
