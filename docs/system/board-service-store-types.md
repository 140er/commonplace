### Board service local types preserve no-store helpers

Board decision queues and approval pages contain different value types. Their local types describe those existing values, including optional approval timing fields, without changing the card or review payloads. Decision buckets stay separate from the returned count.

`BoardService(store=False)` continues to support helpers using a caller's connection. It does not open storage or acquire new write authority. Existing review, version, history and transaction controls remain in place.

Storage operations require an owned store. Attempting one on a no-store service now raises `RuntimeError("board operation requires a store")`, replacing the incidental missing-attribute error. This invalid-operation exception change has its own controls; the compiler prerequisite is proved by checking the unchanged source and the typed source with the same default compiler flags.
