# Commonplace deployment

Keep host configuration outside the source checkout. The tracked Compose and
Caddy files are examples; addresses under `192.0.2.0/24` and `2001:db8::/32`
are documentation placeholders. Replace them before deploying.

## Files

Choose an operator directory and an app checkout beneath it:

```text
/path/to/commonplace/
  .env                 # private environment and credentials; mode 600
  compose.yml          # operator-owned configuration
  deploy.sh            # optional operator-owned deployment wrapper
  data/                # persistent data, owned by UID/GID 10001
  backups/             # completed backup snapshots
  app/                 # release source, replaceable independently of configuration
```

Copy `deploy/compose.host.yml.example` to the operator's `compose.yml` and
`deploy/commonplace.env.example` to `.env`. Set `COMMONPLACE_SOURCE_DIR`,
`COMMONPLACE_ENV_FILE`, and `COMMONPLACE_DATA_DIR` to absolute paths on that
host. Set `COMMONPLACE_BASE_URL` and `COMMONPLACE_ALLOWED_HOSTS` for the URL
clients will use. Do not commit the filled-in files. The corresponding
`deploy/compose.host.yml`, `deploy/commonplace.env`, `deploy/deploy.local.sh`,
and local Caddy copies are ignored when used for local testing.

```bash
export COMMONPLACE_DEPLOY_DIR=/path/to/commonplace
export COMMONPLACE_ENV_FILE="$COMMONPLACE_DEPLOY_DIR/.env"
export COMPOSE_FILE="$COMMONPLACE_DEPLOY_DIR/compose.yml"
chmod 600 "$COMMONPLACE_ENV_FILE"
docker compose --env-file "$COMMONPLACE_ENV_FILE" -f "$COMPOSE_FILE" config --quiet
docker compose --env-file "$COMMONPLACE_ENV_FILE" -f "$COMPOSE_FILE" up -d --build
curl --fail http://127.0.0.1:8765/healthz
```

A deployment wrapper must pass the same external env and Compose paths on
both validation and deployment. `COMPOSE_FILE` must identify the
operator-owned file, not an example in the checkout. Configure the backup
job with the same paths. Preserve `COMPOSE_PROJECT_NAME` when relocating an
existing file so Compose manages the existing container and network.

The service publishes only `127.0.0.1:8765`. Caddy terminates TLS and proxies
requests to that listener. The container runs as `10001:10001`, mounts its
persistent data at `/data`, and has a 3 GiB memory limit. Use a local
filesystem with working SQLite/Chroma locks, encrypted storage where
required, and an off-host backup. Never rebuild or initialize an existing
data directory during an application update.

The image preloads the local MiniLM embedding model as the runtime user.
Its cache is under `$HOME/.cache/chroma`, outside `/data`;
workspace workers link to that cache. A failed preload fails the build.

## Releases and reverse proxy

Stage a reviewed release into the app directory with git or rsync. An rsync
update must leave `.env`, `compose.yml`, and other operator files outside
its destination. Do not use `--delete` against the operator directory.
Keep the previous source release and image tag for rollback.

Copy `deploy/commonplace.caddy.example` or
`deploy/commonplace-public.caddy.example` to your private Caddy site
configuration. Replace bind addresses, hostnames and the static root with
your deployment values. Keep installed site files outside the checkout;
application updates must not replace host-wide Caddy settings. Validate the
complete Caddy configuration before reloading the host service.

The private template routes `/app*`, `/api/*`, `/mcp`, `/health`, `/healthz`,
`/s/*`, `/oauth/*` and `/.well-known/*` to the gateway without stripping paths.
Its fallback serves `landing/public/`. The public template separates the
website, app and agent endpoints; see [PUBLIC.md](../PUBLIC.md).

## Bootstrap and verification

Use the reviewed release's CLI to create the workspace and issue a scoped
token. Never put token values in tracked configuration, shell history,
screenshots or command output. Clients connect to the chosen `/mcp` URL
with `Authorization: Bearer $COMMONPLACE_TOKEN`; stdio-only clients can use
`commonplace connect --url https://hub.example.com/mcp` with that variable.
See `commonplace/agent-skill/SKILL.md` for client integration.

Verify `/healthz`, sign-in, authenticated `tools/list`, memory save/search,
and board access after deploying. Unauthenticated MCP requests must be
refused. A static test does not prove TLS, container startup or recovery.

## Backups

The host's backup scheduler calls executable `app/backup-hook.sh` with an
existing absolute backup directory before its own off-host backup step.
The hook does not configure or invoke restic. Keep credentials, scheduling
and retention policy in the host's private configuration.

By default the hook reads `.env` and `compose.yml` from the parent of its
source checkout. Override that directory with `COMMONPLACE_DEPLOY_DIR`,
the environment file with `COMMONPLACE_ENV_FILE`, or the single Compose
file with `COMPOSE_FILE`. Use absolute paths. The deploy wrapper and backup
scheduler must agree on these files and the Compose project name.

The hook locks the backup directory, checks the backup CLI, stops the
container and its workers, takes a consistent snapshot in a one-off
container, and restarts the gateway even if snapshot creation fails.
Expect a short outage. Staging is owned by `10001:10001` and is mounted at
`/backup` separately from the source `/data`. Only a complete snapshot with
`control.sqlite3` and a valid `manifest.json` is published by atomic rename.
Failure returns nonzero, preserves staging and a mode-600 diagnostic log,
and must abort the parent backup job. Verify failure propagation and an
off-host restore before relying on the scheduler.

## Rollback and recovery

For a code-only rollback, retain live data, restore the previous source
release and image tag, and use the same external Compose/env files:

```bash
docker compose --env-file "$COMMONPLACE_ENV_FILE" -f "$COMPOSE_FILE" \
  up -d --no-build --pull never commonplace
```

Restore the previous installed Caddy site files if routes changed, validate
the full configuration and reload Caddy. Check health, sign-in and MCP.
A wrapper that always rebuilds is unsuitable for image rollback. Keep the
backup hook aligned with the selected image before the next scheduled run.

For data recovery, restore to a separate directory and inspect the backup's
manifest. Stop Commonplace, assemble the complete workspace/control DB
layout, preserve IDs and verbatim data, and set ownership to `10001:10001`.
Retain the previous data directory, switch to the restored sibling and
start its matching pinned image. Never run `init` against recovered data.
Verify health, search, cards and a new backup.

## Local checks

```bash
uv run pytest -q -p no:cacheprovider tests/test_commonplace_deploy.py
uv run ruff check .
bash -n backup-hook.sh
```
