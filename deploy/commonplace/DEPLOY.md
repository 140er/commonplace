# Commonplace on edge1

Tailnet URL: `https://edge1.tail6cee8c.ts.net`. Public hosts remain disabled
until Jeffrey approves them. These are operator instructions, not a record of
a completed deployment. No edge1 connection was made while writing them.

The coordinator's edge1 update supersedes the generic W7 layout: use
`deploy/compose.edge1.yml`, `deploy/commonplace.caddy`, the fixed tailnet
hostname, `/srv/commonplace/app/landing/public`, `/healthz`, and the root
`backup-hook.sh` with host-managed restic. The same update requires public
blocks to remain commented. Accordingly, PR #8's cmmn.dev redirects are
converted but inactive until public exposure is approved; their tests check
the future configuration, not live redirects.

## Prerequisites

The 2026-10-30 03:33 CET edge1 notes report `/srv` LUKS2-encrypted and mounted,
with clevis/tang auto-unlock through `srv-unlock.service`. Verify the mount
before each deployment. Do not create directories on the underlying root
filesystem when `/srv` is locked or unmounted.

The admin supplies Docker Compose, Caddy's existing Tailscale TLS setup,
`/srv/commonplace/deploy.sh`, `/srv/commonplace/.env` (owner `jeffrey`, mode
600), and `edge1-backup.timer`. Keep Caddy's existing global configuration.
Host utilities needed by the backup hook: bash, Python 3, flock, and GNU
coreutils. Retention and restic credentials belong to the host administrator.

The release must include W5's `login-link` and `backup` commands and the
gateway's `/healthz` alias. They are integration prerequisites; the base W7
checkout does not implement them. The preflight below refuses an incomplete
release. No CLI, gateway, or storage code is supplied by this deploy change.

## Stage a reviewed release

On the source machine, from a clone containing the reviewed release and the
website PR #8 commit (fetch the refs first if needed):

```bash
RELEASE_REV='<reviewed Commonplace release tag or full commit SHA>'
SITE_REV=5de27e1541e9e7640215f3982bc0fe07feabe426
RELEASE_SHA=$(git rev-parse "$RELEASE_REV^{commit}")
release_dir=$(mktemp -d)
git archive "$RELEASE_SHA" | tar -x -C "$release_dir"
# PR #8 owns the website. Overlay its artifact without changing its worktree.
git archive "$SITE_REV" landing/public | tar -x -C "$release_dir"
printf '%s\n' "$RELEASE_SHA" > "$release_dir/deploy/REVISION"
test -s "$release_dir/landing/public/index.html"
test -s "$release_dir/landing/public/connect/index.html"
test -x "$release_dir/backup-hook.sh"
# Only after the admin has confirmed the encrypted mount:
rsync -az --delete --exclude .git --exclude .venv --exclude node_modules \
  --exclude target "$release_dir/" edge1:/srv/commonplace/app/
rm -rf -- "$release_dir"
```

All commands below run in an **existing edge1 operator shell**. Deployment
does not create an SSH connection. First verify the mount and provision only
the app's writable directory; never recursively chown `/srv`:

```bash
mountpoint -q /srv || exit 1
findmnt /srv
cd /srv/commonplace/app
sudo install -d -o 10001 -g 10001 -m 0700 /srv/commonplace/data
sudo install -d -o root -g root -m 0700 /srv/commonplace/backups
chmod 600 /srv/commonplace/.env
chmod +x backup-hook.sh
cat deploy/REVISION
```

Preserve the existing `.env` secrets. Add/update these entries in that file;
use the exact SHA printed above as the image tag:

```dotenv
COMMONPLACE_BASE_URL=https://edge1.tail6cee8c.ts.net
COMMONPLACE_IMAGE_TAG=<full release SHA>
```

Use the same environment file for every Compose command, including backups:

```bash
export COMPOSE_FILE=deploy/compose.edge1.yml
export COMPOSE_ENV_FILES=/srv/commonplace/.env
docker compose --env-file /srv/commonplace/.env config --quiet
```

Compose resolves `context: ..` relative to `deploy/compose.edge1.yml`, so
the build context is the repository root. Port 8765 binds only to loopback;
the container listens on 0.0.0.0 inside its network namespace. The image runs
as UID/GID 10001 and uses the host bind `/srv/commonplace/data:/data`.

## Build, initialize, start

On upgrades, retain the current image before a build:

```bash
previous_image=$(docker compose --env-file /srv/commonplace/.env images -q commonplace)
if [ -n "$previous_image" ]; then
  docker image tag "$previous_image" commonplace:rollback
fi
```

For a first install, build and check the required CLI before starting:

```bash
docker compose --env-file /srv/commonplace/.env build commonplace
docker compose --env-file /srv/commonplace/.env run --rm --no-deps commonplace commonplace login-link --help
docker compose --env-file /srv/commonplace/.env run --rm --no-deps commonplace commonplace backup --help
```

The build installs the repository with `pip install .` and embeds one test
string using the default MiniLM model. Its downloaded cache lives in
`/home/commonplace/.cache/chroma`, outside the data mount; workspace workers
reuse it. No user data is needed for the download. Other embedding models
are not preloaded. Keep the reviewed image for rollback: dependency versions
are constrained by the repository, not locked by this Dockerfile.

Initialize **once**, when `/srv/commonplace/data/control.sqlite3` has not
been initialized:

```bash
docker compose --env-file /srv/commonplace/.env run --rm --no-deps commonplace \
  commonplace init --admin-email jeffrey@example.com --workspace personal
COMPOSE_FILE=deploy/compose.edge1.yml /srv/commonplace/deploy.sh
docker compose --env-file /srv/commonplace/.env ps
```

Use Jeffrey's actual email. `serve` exits when there are no users, so first
initialization uses a one-off container. The corresponding command for an
already-running container is `docker compose --env-file
/srv/commonplace/.env exec -T commonplace commonplace init --admin-email
jeffrey@example.com --workspace personal`; do not rerun it on initialized
data. On subsequent releases, skip init and run `deploy.sh` as above. It
checks loopback bindings and builds/starts the service. Do not prune old
images until the release and its backup have been verified.

## Install Caddy routing

The release directory already contains `landing/public` from PR #8. Caddy
must be able to traverse `/srv/commonplace/app/landing/public` and read its
files; do not grant it access to `/srv/commonplace/data` or `.env`.

```bash
sudo cp -a /etc/caddy/sites/commonplace.caddy /etc/caddy/sites/commonplace.caddy.rollback
sudo install -o root -g root -m 0644 deploy/commonplace.caddy /etc/caddy/sites/commonplace.caddy
sudo caddy fmt --overwrite /etc/caddy/sites/commonplace.caddy
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
curl --fail --silent --show-error https://edge1.tail6cee8c.ts.net/healthz
curl --fail --silent --show-error -o /dev/null https://edge1.tail6cee8c.ts.net/
curl --fail --silent --show-error -o /dev/null https://edge1.tail6cee8c.ts.net/app/
```

The tailnet site explicitly preserves `/app*`, `/api/*`, `/mcp`, `/health`
and `/healthz` when proxying. The fallback serves the website directly.
Streaming responses use `flush_interval -1`. The static site's CSP applies
only to static pages; the app supplies its own policy. There is no new HTTP
fallback or public hostname. The commented cmmn.dev short links reproduce
PR #8's destinations and 302 status codes, including its default homepage
redirect; they do not redirect the tailnet site's `/connect/` page.

## Admin access and client tokens

```bash
docker compose --env-file /srv/commonplace/.env exec -T commonplace \
  commonplace login-link jeffrey@example.com --workspace personal \
  --base-url https://edge1.tail6cee8c.ts.net
```

Open the printed one-use link on a tailnet-connected device. It expires in
15 minutes. Keep login links and connection tokens out of logs and tickets.

These commands use explicit policies, which work both before and after the
W5 client presets. Coding clients can read non-private themes and write
`project_*` and `shared`; Grok can read `shared,inbox` and writes only `inbox`.
Knowledge-graph access is excluded by the explicit memory-only scopes.

```bash
docker compose --env-file /srv/commonplace/.env exec -T commonplace \
  commonplace token add claude-code:edge1 --workspace personal --client claude-code \
  --profile standard --scopes memory:read,memory:write \
  --read-wings '*' --write-wings 'project_*,shared' --trust trusted
docker compose --env-file /srv/commonplace/.env exec -T commonplace \
  commonplace token add codex:edge1 --workspace personal --client codex \
  --profile standard --scopes memory:read,memory:write \
  --read-wings '*' --write-wings 'project_*,shared' --trust trusted
docker compose --env-file /srv/commonplace/.env exec -T commonplace \
  commonplace token add grok:edge1 --workspace personal --client grok \
  --profile slim --scopes memory:read,memory:write \
  --read-wings shared,inbox --write-wing inbox --trust untrusted
```

Each token is printed once. Store it in that client's secret environment as
`COMMONPLACE_TOKEN`, and configure its HTTP MCP endpoint as
`https://edge1.tail6cee8c.ts.net/mcp` with `Authorization: Bearer`.
Clients supporting only stdio can use W5's
`commonplace connect --url https://edge1.tail6cee8c.ts.net/mcp` with that
environment variable; install the same reviewed release on the client.
See `commonplace/agent-skill/SKILL.md` for client integration.

Verify with one issued token in an operator shell (the prompt does not echo
the token or put it in command history):

```bash
read -r -s -p 'Token: ' COMMONPLACE_TOKEN; printf '\n'
export COMMONPLACE_TOKEN
python3 - <<'PY'
import json, os, urllib.request
request = urllib.request.Request(
    'https://edge1.tail6cee8c.ts.net/mcp',
    data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).encode(),
    headers={'Content-Type': 'application/json',
             'Authorization': 'Bearer ' + os.environ['COMMONPLACE_TOKEN']},
)
with urllib.request.urlopen(request, timeout=120) as response:
    result = json.load(response)
assert 'error' not in result, result
names = {tool['name'] for tool in result['result']['tools']}
assert {'memory_save', 'memory_search'} <= names, names
print('\n'.join(sorted(names)))
PY
unset COMMONPLACE_TOKEN
```

## Backups

The existing host `edge1-backup.timer` runs nightly at 03:00 CET. It calls
executable `/srv/commonplace/app/backup-hook.sh <backups_dir>` **before**
host-managed restic sends `/etc /srv /var/lib/caddy` to zurvur over Tailscale.
Do not add a second scheduler, restic invocation, or restic credentials to
Compose. The administrator owns repository initialization and credentials
in `/root/.config/restic/`, and must confirm the parent job aborts on a
nonzero hook exit. Retention/prune runs on zurvur (append-only on edge1):
request/verify **30 daily and 12 weekly** there.

These host settings have not been inspected by W7. The notes document the
zurvur endpoint (`100.91.208.60:8000`), nightly schedule and host-managed
credentials, but do not establish the actual `RESTIC_REPOSITORY`, readable
`RESTIC_PASSWORD_FILE`, 30-daily/12-weekly retention, or parent-job failure
propagation. The administrator must verify those settings and a successful
off-host restore before backup integration can be considered complete.

The hook uses the deployed image tag from `/srv/commonplace/.env`, checks
that `backup` exists, obtains a lock, stops the gateway container and all
its worker processes, and runs `commonplace backup` in a one-off container.
It restarts the gateway even on snapshot failure. Expect a short service
outage. An exec'd CLI cannot stop workers belonging to the live gateway;
copying data while they write would not be a consistent backup.

The root-run hook creates `<backups_dir>/.commonplace-staging` owned by
10001:10001 and mounts it at `/backup` only in the one-off container. The
source `/data` remains separate. Snapshot paths are `/backup/<UTC
timestamp>-<pid>` inside the container and `<backups_dir>/.commonplace-staging/<same>`
on the host. The parent exists; each destination is new, as the CLI requires.
After successful backup, restart, and manifest/control-DB checks, the hook
atomically moves the whole snapshot to `<backups_dir>/commonplace-<same>`.
The backups directory must be an existing absolute directory on encrypted
`/srv`. Failure preserves staging and a mode-600 log in `/tmp`, emits
one failure line, and returns nonzero. Never treat a failed staging tree as
a usable recovery point. The backup command must write consistent SQLite
copies, complete workspace data and `manifest.json` per CONTRACT.md.

```bash
sudo systemctl enable --now edge1-backup.timer
sudo systemctl start edge1-backup
sudo journalctl -u edge1-backup -n 50 --no-pager
systemctl list-timers edge1-backup.timer
sudo ls -l /srv/commonplace/backups/
```

Have the host administrator confirm the restic snapshot exists on zurvur;
a local snapshot alone does not verify off-host recovery. Do a restore
drill before relying on this job.

## Rollback and recovery

For a code-only rollback, keep the current data, set
`COMMONPLACE_IMAGE_TAG=rollback` in `/srv/commonplace/.env`, and run from
`/srv/commonplace/app` with `COMPOSE_FILE=deploy/compose.edge1.yml`:

```bash
docker compose --env-file /srv/commonplace/.env up -d --no-build --pull never commonplace
sudo cp -a /etc/caddy/sites/commonplace.caddy.rollback /etc/caddy/sites/commonplace.caddy
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
curl --fail https://edge1.tail6cee8c.ts.net/healthz
```

Do not use `deploy.sh` for that rollback: it rebuilds. Restage the prior
reviewed release and pinned website artifact for a permanent rollback. Keep
its backup hook and code aligned with the chosen image before the next timer.
If the release changed storage formats, restore its matching backup as well.

For data recovery, have the admin restore the selected restic snapshot to
a **separate** directory on encrypted `/srv`, select a completed
`backups/commonplace-<timestamp>-<pid>` inside it, inspect `manifest.json`,
and stop Commonplace. The manifest defines the backup's file layout; do not
copy a snapshot blindly over live data. Assemble a fresh sibling data
directory with `control.sqlite3` and `workspaces/<id>/...` as described by
the manifest, excluding the manifest itself and any backup-staging tree.
Preserve IDs and every workspace's databases, Memory files, configuration
and home data. Chown the restored tree to 10001:10001. Retain the old data
directory by renaming it, move the restored tree to `/srv/commonplace/data`,
then start the matching pinned image with `up -d --no-build --pull never`.
Verify `/healthz`, sign-in, MCP search, cards and a new backup. Never run
`init` against recovered data. W5 owns the manifest schema; use the merged
release's schema when carrying out the restore.

## Later public exposure

Only after Jeffrey's explicit OK: review DNS, Caddy certificates, authentication,
and backup restore results; then enable the commented public blocks, set
`COMMONPLACE_BASE_URL=https://app.cmmn.dev` in `.env`, recreate the service
and validate/reload Caddy. This environment variable configures Commonplace,
not the host Caddy process. Short links retain PR #8's commonplace.run targets;
that website hostname needs its own separately approved hosting plan. Do not
enable a short-link service that points at an unprovisioned website.

## Local validation

```bash
~/work/mempalace/.venv/bin/python -m pytest -q -p no:cacheprovider -x tests/test_commonplace_deploy.py
~/work/mempalace/.venv/bin/python -m ruff check tests/test_commonplace_deploy.py
bash -n backup-hook.sh
# If Docker is installed, and free -m reports at least 4096 MB available:
docker build -f deploy/commonplace/Dockerfile -t commonplace:check .
# On edge1, with the real env file:
docker compose --env-file /srv/commonplace/.env -f deploy/compose.edge1.yml config --quiet
# With Caddy and its existing global Tailscale config installed:
caddy fmt --diff deploy/commonplace.caddy
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Static tests do not prove container startup, Caddy TLS, off-host backups or
an end-to-end deployment. Run the operator verification above after integration.
