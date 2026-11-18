### The gateway's ten-minute memory sawtooth is a push onto a long saved thread

A clean window on the gateway showed anonymous memory rising about 430 MiB within minutes and dropping about every ten minutes, with threads and descriptors flat. No background timer explains it. The drop is the heap trim that runs when an idle thread store gives its page cache back (every ten minutes, `COMMONPLACE_STORE_IDLE_S`); the rise is the allocator keeping what a request freed.

The request is a capture push onto a long thread. A client that syncs a running agent session sends only the new lines, but the old code read the whole saved thread several times to accept them: every message loaded at once (archive text, parsed text and raw lines, about 2.5 times the transcript), the archive joined into bytes twice, and the thread digest built as one JSON string and encoded, three times. On a 20 MiB thread that is about 315 MiB of Python allocations for a 50 KiB suffix, and glibc keeps the freed pages until a trim.

Decision: stream it. The digest is hashed per message and per megabyte; the row's stored digest replaces the read-back comparison; new rows are appended without loading the old ones; the archive is read once, a page at a time; and an upload of 4 MiB or more is followed by a heap trim. Stored digests, archives and wire format are unchanged.

| Existing thread | Gateway anonymous peak before | after | Traced peak, thread 20.6 MiB (v1 / v2) |
| --- | ---: | ---: | --- |
| 30 MiB, +50 KiB push | 723 MiB (+504 over start) | 188 MiB (+136) | 315 / 251 MiB before, 67 / 125 MiB after |

Memory retained between pushes fell too: after the first appends the old code sat at 343 MiB before each push, the new code at 54 MiB. An idle local gateway with `COMMONPLACE_MEMTRACE=1` for 25 minutes showed no periodic allocation: the second snapshot grew by well under 1 MiB per site, so the diagnostic itself, not a job, is the only ten-minute event on an idle gateway (one snapshot costs about 90 MiB transiently on a small fixture). What remains: the first upload of a whole large transcript still holds its text several times; thread store v2 still decompresses a block at a time.
