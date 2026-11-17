- **Owns:**

  - `.github/workflows/ci.yml` runs `python -m commonplace.guards.prepush --ci` for the pull-request test gate. `.devcontainer/post-create.sh` and `.github/PULL_REQUEST_TEMPLATE.md` point at `python -m commonplace.guards.prepush --manual --base origin/develop`.
