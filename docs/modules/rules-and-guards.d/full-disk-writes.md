- **How it fails:** A REST write that fails also records a `deny` audit row; if that audit
  write fails too (a full disk), the failure is logged and the original error is the one the
  caller sees.
