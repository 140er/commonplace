#!/usr/bin/env bash
# Extract a trusted, reviewed git source archive into a NEW source directory.
# Release selection/attestation and activation belong to the caller.
set -Eeuo pipefail

if [[ $# != 2 ]]; then
    echo 'usage: stage-archive.sh SOURCE.tar NEW_SOURCE_DIR' >&2
    exit 2
fi
archive=$1
destination=$2
[[ -f $archive ]] || { echo 'source archive must be a regular file' >&2; exit 1; }
# Never extract over an existing tree: it may contain runtime secrets or data.
mkdir -m 755 -- "$destination"
# GNU tar normally masks archive modes for unprivileged callers. Restore only
# archive members in this fresh source tree, never permissions on runtime paths.
tar --extract --file "$archive" --directory "$destination" \
    --no-same-owner --same-permissions
# A git archive carries its commit in the tar header. Keep it beside the source
# so the image build can name the commit it was built from.
if command -v git >/dev/null; then
    commit=$(git get-tar-commit-id < "$archive" 2>/dev/null) || commit=
    if [[ $commit =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
        printf '%s\n' "$commit" > "$destination/.release-commit"
    fi
fi
