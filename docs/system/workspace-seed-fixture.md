### Reuse closed workspace fixture seeds

Workspace store-switch tests build their identical synthetic starting databases once per module, after the requesting case's deterministic embedding fixture is active. Every case receives a separate file copy; stores close before copying, and the seed supplies no live connections. Switches, backup commands, migrations, verification, crash recovery and assertions retain their original workload. A regression checks file identity and byte isolation across the seed and sibling copies after real writes.
