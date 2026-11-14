### Drawer batch response scrubber

- Batch drawer reads apply the same response-local scrubber through a shared
  internal helper. Input order, missing-drawer errors, counts and citation proof
  remain unchanged. Hallway responses retain their independent endpoint proof.
