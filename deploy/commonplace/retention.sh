#!/usr/bin/env bash
# Post-deploy retention for the Commonplace host: old images, build cache and
# staged releases. Run it after a healthy deploy, as root, on the host.
#
# Keeps, per kind (commonplace:develop-*, commonplace:rollback-before-*), the
# newest DEPLOY_RETENTION_IMAGES tags (default 5). Never removes the image the
# running container uses, the image the active compose file pins, or the
# rollback image for the running release (rollback-before-<running sha>).
# Tags are removed one by one with `docker image rm` (never -f, never
# `image prune -a`), so an image a container still uses cannot go.
# Then prunes build cache older than DEPLOY_BUILDER_PRUNE_UNTIL (default 24h)
# and keeps the newest DEPLOY_RETENTION_RELEASES release dirs (default 10)
# plus the build contexts of every protected image.
#
# It only touches the commonplace image repository, the build cache and
# $DEPLOY_RELEASES_DIR. Data, backups, volumes and containers are never touched.
# Failures only warn: retention never fails a deploy.
#
# DEPLOY_RETENTION_DRY_RUN=1 prints what it would remove and removes nothing.
# COMMONPLACE_DEPLOY_LOCK_HELD=1 means the caller already holds the deploy lock.
set -Euo pipefail

say() { printf '[retention] %s\n' "$*"; }
warn() { printf '[retention] WARNING: %s\n' "$*" >&2; }

positive_int() { [[ $2 =~ ^[1-9][0-9]{0,3}$ ]] || { warn "invalid $1, nothing removed"; exit 0; }; }

low() {
    local -a prefix=()
    command -v ionice >/dev/null && prefix+=(ionice -c3)
    command -v nice >/dev/null && prefix+=(nice -n 19)
    "${prefix[@]}" "$@"
}

act() {
    if [[ $DRY_RUN == 1 ]]; then
        say "dry-run: $*"
        return 0
    fi
    low "$@" >/dev/null
}

main() {
    [[ $(id -u) == 0 ]] || { warn 'must run as root, nothing removed'; exit 0; }
    COMMONPLACE_ROOT=${COMMONPLACE_ROOT:-/srv/commonplace}
    KEEP_IMAGES=${DEPLOY_RETENTION_IMAGES:-5}
    KEEP_RELEASES=${DEPLOY_RETENTION_RELEASES:-10}
    PRUNE_UNTIL=${DEPLOY_BUILDER_PRUNE_UNTIL:-24h}
    RELEASES_DIR=${DEPLOY_RELEASES_DIR:-$COMMONPLACE_ROOT/releases}
    CONTAINER=${DEPLOY_CONTAINER:-deploy-commonplace-1}
    REPO=commonplace
    DRY_RUN=${DEPLOY_RETENTION_DRY_RUN:-0}
    positive_int DEPLOY_RETENTION_IMAGES "$KEEP_IMAGES"
    positive_int DEPLOY_RETENTION_RELEASES "$KEEP_RELEASES"
    [[ $PRUNE_UNTIL =~ ^[1-9][0-9]{0,4}(h|m)$ ]] || { warn 'invalid DEPLOY_BUILDER_PRUNE_UNTIL, nothing removed'; exit 0; }
    [[ $RELEASES_DIR == /* ]] || { warn 'DEPLOY_RELEASES_DIR must be absolute, nothing removed'; exit 0; }

    if [[ ${COMMONPLACE_DEPLOY_LOCK_HELD:-0} != 1 ]]; then
        { exec 9>"$COMMONPLACE_ROOT/.deploy.lock"; } 2>/dev/null || { warn 'cannot open deploy lock, nothing removed'; exit 0; }
        flock -n 9 || { warn 'a deploy is running, nothing removed'; exit 0; }
    fi

    # Protected image IDs. Without the running image we stop: never guess.
    local running_ref running_id rollback_id pinned id
    running_ref=$(docker inspect -f '{{.Config.Image}}' "$CONTAINER" 2>/dev/null) || running_ref=
    running_id=$(docker inspect -f '{{.Image}}' "$CONTAINER" 2>/dev/null) || running_id=
    [[ $running_id == sha256:* ]] || { warn "cannot read the image of $CONTAINER, nothing removed"; exit 0; }
    local -A protected=([$running_id]=1)
    if [[ $running_ref =~ ^$REPO:develop-([0-9a-f]{7,40})$ ]]; then
        rollback_id=$(docker image inspect -f '{{.Id}}' "$REPO:rollback-before-${BASH_REMATCH[1]}" 2>/dev/null) || rollback_id=
        [[ -z $rollback_id ]] || protected[$rollback_id]=1
    fi
    if [[ -n ${COMPOSE_FILE:-} && -f $COMPOSE_FILE ]]; then
        while read -r pinned; do
            id=$(docker image inspect -f '{{.Id}}' "$pinned" 2>/dev/null) || continue
            protected[$id]=1
        done < <(sed -nE 's/^[[:space:]]+image:[[:space:]]*"?([^"[:space:]]+)"?.*$/\1/p' "$COMPOSE_FILE")
    fi

    # Images: newest KEEP_IMAGES tags per kind stay, protected IDs always stay.
    local kind n tag
    for kind in develop rollback-before; do
        n=0
        while IFS=$'\t' read -r _ tag id; do
            [[ $tag == "$REPO:$kind-"* ]] || continue
            [[ $tag =~ ^$REPO:$kind-[0-9a-f]{7,40}$ ]] || continue
            n=$((n + 1))
            (( n > KEEP_IMAGES )) || continue
            [[ -z ${protected[$id]:-} ]] || { say "keep protected $tag"; continue; }
            act docker image rm "$tag" || warn "could not remove $tag"
            [[ $DRY_RUN == 1 ]] || say "removed $tag"
        done < <(docker image ls --no-trunc --format '{{.CreatedAt}}'$'\t''{{.Repository}}:{{.Tag}}'$'\t''{{.ID}}' "$REPO" 2>/dev/null | sort -r)
    done

    act docker builder prune -f --filter "until=$PRUNE_UNTIL" || warn 'builder prune failed'

    # Releases: build contexts of protected images stay, plus the newest KEEP_RELEASES.
    [[ -d $RELEASES_DIR ]] || { say 'no releases dir, done'; exit 0; }
    local -A keep_release=()
    local rev dir name
    for id in "${!protected[@]}"; do
        rev=$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$id" 2>/dev/null) || rev=
        [[ ! $rev =~ ^[0-9a-f]{40}$ ]] || keep_release[$rev]=1
    done
    n=0
    while IFS= read -r dir; do
        name=${dir##*/}
        [[ $name =~ ^([0-9a-f]{40})(\.stale-[0-9-]+)?$ ]] || continue
        n=$((n + 1))
        (( n > KEEP_RELEASES )) || continue
        [[ -n ${BASH_REMATCH[2]} || -z ${keep_release[${BASH_REMATCH[1]}]:-} ]] || { say "keep protected release $name"; continue; }
        act rm -rf -- "$RELEASES_DIR/$name" || warn "could not remove release $name"
        [[ $DRY_RUN == 1 ]] || say "removed release $name"
    done < <(find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2-)
    say finished
}

main "$@"
