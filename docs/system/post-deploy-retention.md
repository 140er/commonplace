### Post-deploy retention

After a verified healthy deploy the production host can clean up what deploys leave behind: old images, build cache and staged release directories. It is off by default (`DEPLOY_RETENTION=1` turns it on in the deploy settings file) and runs from `deploy/commonplace/retention.sh`, inside the deploy lock the deploy already holds. Run on its own it takes the same lock without waiting and does nothing while a deploy is running.

**Kept:** the running image, the image the active compose file pins, and the rollback image of the running release; the 5 newest `develop-*` and the 5 newest `rollback-before-*` images; the 10 newest staged releases plus the build contexts of the protected images; build cache newer than 24 hours. The counts and the cache age are settings (`DEPLOY_RETENTION_IMAGES`, `DEPLOY_RETENTION_RELEASES`, `DEPLOY_BUILDER_PRUNE_UNTIL`).

**Never touched:** data, backups, volumes and containers. Images are removed one tag at a time without force, so an image a container still uses stays.

**Failures only warn.** Retention never fails a deploy; it removes nothing when it is not root, cannot read the running image, or finds an invalid setting. `DEPLOY_RETENTION_DRY_RUN=1` prints what would go and removes nothing.
