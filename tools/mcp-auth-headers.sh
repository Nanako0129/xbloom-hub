#!/bin/sh
# Prints the Authorization header for the hub as JSON, for Claude Code's
# `headersHelper`. Reads the token from a file so it never sits in a config
# file, an environment variable or a command line.
set -eu
file="${XBLOOM_HUB_TOKEN_FILE:-secrets/hub_token}"
[ -s "$file" ] || { echo "xbloom-hub: no token in $file" >&2; exit 1; }
exec python3 -c 'import json, sys; print(json.dumps({"Authorization": "Bearer " + open(sys.argv[1]).read().strip()}))' "$file"
