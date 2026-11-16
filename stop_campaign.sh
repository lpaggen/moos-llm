#!/usr/bin/env bash

set -Eeuo pipefail

LOCK_FILE="${TMPDIR:-/tmp}/moos-llm-campaign.lock"

if flock -n "$LOCK_FILE" true; then
    echo "No MOOS campaign is running."
    exit 0
fi

echo "Stopping the active MOOS campaign..."
fuser -k -TERM "$LOCK_FILE" 2>/dev/null || true

for _ in {1..10}; do
    if flock -n "$LOCK_FILE" true; then
        echo "Campaign stopped; lock released."
        exit 0
    fi
    sleep 1
done

echo "Campaign did not stop cleanly; force-killing remaining processes..." >&2
fuser -k -KILL "$LOCK_FILE" 2>/dev/null || true
sleep 1

if flock -n "$LOCK_FILE" true; then
    echo "Campaign stopped; lock released."
    exit 0
fi

echo "Unable to release $LOCK_FILE" >&2
fuser -v "$LOCK_FILE" >&2 || true
exit 1
