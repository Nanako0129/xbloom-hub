#!/bin/sh
# Every npm command for the dashboard runs here, in a throwaway container,
# never on the host (docs/PLAN.md, S3c, finding C1): building runs the code of
# every package in the tree, and the host holds the owner's credentials.
#
#   frontend/npm.sh ci            install exactly what package-lock.json says
#   frontend/npm.sh install x@1.2.3   add a package (exact versions only)
#   frontend/npm.sh run build     build into src/xbloom_hub/web/
#   frontend/npm.sh run check     type-check
#
# Only frontend/ and the output directory are mounted; node_modules lives in a
# named volume, not in the host tree. No home directory, no ssh agent, no
# environment from the host, no secrets/. The network is on only for the
# commands that fetch packages.
set -eu

# node 24.22.3, npm 11.19.1, Alpine; pinned by digest, not by tag.
IMAGE="node@sha256:116b800c3754b440ee0aa3ed2f20f5bf3d99a554bfd9adeea09ea8017f6e85f5"
VOLUME="xbloom-hub-node-modules"

here=$(cd "$(dirname "$0")" && pwd)
out="$here/../src/xbloom_hub/web"
mkdir -p "$out"
out=$(cd "$out" && pwd)

network=none
case "${1:-}" in
  ci|install|i|audit|view|outdated) network=bridge ;;
esac

exec docker run --rm -i \
  --network "$network" \
  --cap-drop ALL --security-opt no-new-privileges \
  -v "$here:/app/frontend" \
  -v "$out:/app/src/xbloom_hub/web" \
  -v "$VOLUME:/app/frontend/node_modules" \
  -w /app/frontend \
  "$IMAGE" npm "$@"
