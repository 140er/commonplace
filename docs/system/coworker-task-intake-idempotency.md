### Coworker task intake idempotency

The internal person-only `BoardService.ensure_chat_task(origin_key, request)`
creates the canonical card and actor-scoped opaque origin binding in one boards
transaction. Exact request retries return the same visible card; changed board,
title or body conflicts. Distinct origin keys preserve legitimately repeated
requests. No private conversation text is stored in the binding or copied into
Space chat; the caller must obtain explicit intent before sharing requested task
text with the selected board. This internal seam adds no public agent tool or
permission and rechecks existing board write authority on replay.
