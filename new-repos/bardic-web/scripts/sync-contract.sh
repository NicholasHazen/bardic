#!/bin/sh
# Copy the contract from a checkout of bardic-server. Usage: SERVER_REPO=../bardic-server npm run contract:sync
set -eu
SERVER_REPO="${SERVER_REPO:-../bardic-server}"
SRC="$SERVER_REPO/docs/contract/openapi.yaml"
[ -f "$SRC" ] || { echo "not found: $SRC (set SERVER_REPO)"; exit 1; }
cp "$SRC" contract/openapi.yaml
grep -m1 '^  version:' contract/openapi.yaml | sed 's/.*version: *//' > contract/VERSION
echo "contract synced: $(cat contract/VERSION)"
