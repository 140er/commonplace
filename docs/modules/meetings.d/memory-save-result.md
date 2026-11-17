- **Owns:** The shared memory save result consumed by meetings/filing.py.
- **Promises:** Keeping a meeting note unpacks the five-value _save result and uses
  its stored drawer id, preserving the existing single write and replay behavior.
- **How it fails:** Memory refusals keep the existing filing refusal and release
  the filing claim; successful writes no longer fail on an obsolete tuple shape.
- **Tests:** tests/test_meetings_notes.py covers keeping a note, its provenance,
  authorization, error recovery and repeated filing without duplicate writes.
