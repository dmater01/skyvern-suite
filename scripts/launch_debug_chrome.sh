#!/usr/bin/env bash
PROFILE_DIR="$HOME/Library/Application Support/Google/Chrome/SkyvernProfile"
mkdir -p "$PROFILE_DIR"
echo "Starting Chrome with remote debugging on port 9222..."
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check &
