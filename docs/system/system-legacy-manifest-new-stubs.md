### Manifest entries for later moved decisions

A decision moved out of `SYSTEM.md` after the original split still needs a manifest
entry, because the one-to-one check requires every stub to be listed. The entry
follows the earlier ones: title, slug, occurrences, size and SHA-256 of the file in
`docs/system/`. The gateway vector diagnostics stub was added without one and is now
listed in the last manifest. The check itself is unchanged.
