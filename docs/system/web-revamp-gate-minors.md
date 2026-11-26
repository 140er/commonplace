### Web UI follow-ups: bounded Work reads, colour role names, plain setting copy

- The Work screen read one cards request per project at once on every refresh, so a large workspace opened a burst of parallel requests. Decision: no aggregate cards endpoint exists, so the screen keeps the per-project reads and runs at most four at a time (`CARD_READS`), in project order, with failures reported per project.
- The Deck stylesheet called the steel-blue accent `--violet` and the green `--cyan`. Decision: the roles are `--primary`, `--primary-ink` and `--ok`; the old names stay as aliases for one release so per-screen sheets keep working, and the shared sheet reads only the new names.
- The old Home hero (`deck-home.js`) was no longer mounted: Home is the Waiting on you screen. Decision: delete the module and its asset-route entry, and keep the checks that describe live behaviour (the rail and phone entries for Home, the shared waiting count).
- The "machine is not attested" refusal and the Attest form hint named a settings file path. Decision: say where the setting lives in plain words (the T3 settings stored on that machine) and show no path.
- Not changed: the consent Details presentation, which needs an owner decision.
