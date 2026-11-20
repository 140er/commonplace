### Batch immutable documentation fragment reads

Documentation checks list every fragment as before, then read each directory's blob IDs through one Git batch process. The batch is local to that read and preserves exact blob framing before the existing UTF-8 replacement decoding. There is no persistent cache or mutable-ref cache.

The module map, fragment integrity, coverage, changed ownership, both revisions' documentation text and base-listed commit waivers retain their existing checks. Missing objects, failed Git commands and incomplete or malformed batch replies fail closed. Other Git object kinds retain their existing read behavior.

Reducing fragment subprocesses improves the documentation portion of a changed-file check. Its complete elapsed time also includes formatting, lint, typechecking and mapped tests; those checks remain required and the elapsed target remains an alarm.
