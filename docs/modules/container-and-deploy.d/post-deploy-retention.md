- **Owns:**

  - `deploy/commonplace/retention.sh` and its opt-in hook in `deploy/commonplace/deploy.sh`: post-deploy cleanup of old `commonplace` images, build cache and staged release directories, configured through the `DEPLOY_RETENTION*`, `DEPLOY_RELEASES_DIR` and `DEPLOY_BUILDER_PRUNE_UNTIL` keys in `deploy/commonplace/deploy.env.example`.

- **Promises:**

  - Retention runs only when `DEPLOY_RETENTION=1` (default off), only after a verified healthy release, and under the deploy lock the deploy already holds; run alone it takes that lock without waiting and skips while a deploy is running.
  - The running image, the image the active compose file pins and the rollback image of the running release are always kept. Besides those, the 5 newest `develop-*` and 5 newest `rollback-before-*` images stay, the 10 newest staged releases plus the build contexts of protected images stay, and build cache older than 24 hours is pruned.
  - Data, backups, volumes and containers are never touched. Images go one tag at a time with a plain `docker image rm` (never forced, never `image prune -a`), so an image a container still uses cannot be removed.
  - `DEPLOY_RETENTION_DRY_RUN=1` prints what would be removed and removes nothing.

- **How it fails:**

  - Retention only warns and never fails a deploy: a missing script, a non-zero exit or a single failed removal is a warning line. It removes nothing when it is not root, when the running image cannot be read, when a setting is invalid, or when the deploy lock is held.

- **Tests:**

  - `tests/test_commonplace_retention.py` runs the script against a fake `docker`: newest-per-kind and protected images, release directories, dry run, fail-safe cases and the held lock.
