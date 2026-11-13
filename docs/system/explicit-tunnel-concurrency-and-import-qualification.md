### Explicit tunnel concurrency and import qualification

- Tunnel probes use identity keysets from one file read per worker call. Deleting
  an earlier id cannot shift later pages. Final identity lookup compares only the
  id, label and endpoint wing/room/drawer associations and returns the current row, so
  traversal potentiation and unrelated drawer metadata updates remain visible.
- Missing or changed associations still fail closed before preview hydration or
  traversal recording. Probe and allow batches remain bounded and request-local.
  Invalid name arguments retain the ordinary validation error envelope.
- Computed `imported:` drawers have no stored metadata association and are excluded
  from explicit tunnels even when the existing import reader proves their consent
  and source range. Ordinary imported-history read APIs retain their range rules.
- The read-only server seam used by the regression witness passes `record=False`;
  the public follow schema does not expose that argument. Tunnel labels now use
  independent protected-token scans before listing, following or counting them.
