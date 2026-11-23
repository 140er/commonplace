### Coworker transient credential redaction

Coworker model input uses the existing front-chat credential redactor on separate outbound content copies. Bot name, persona, current text, selected memory text and history text are redacted before the provider envelope is serialized. The complete persona is redacted before its existing display/input slice.

Stored bot settings, messages, history and memories retain their original values. Selected-memory snapshots retained for freshness checks also stay original, so removing or changing a memory still withholds a late answer. Configured provider credentials remain in their existing key/header channel. Explicit task title/body continue through their existing durable task branch.

The existing input budget measures the sanitized system and JSON payload. Redaction expansion can refuse required input or exclude optional memory/history without changing the budget, relevance ranking or authority/configuration gates.

The finite synthetic regression observes actual worker serialization with a transport capture and denied connector, and compares the retained originals. This boundary establishes no provider activation or runner-admission change.
