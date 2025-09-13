#!/usr/bin/env bash
set -euo pipefail

LABEL_DEFAULT="com.hotmic"
LABEL="${1:-$LABEL_DEFAULT}"
if [[ $# -eq 0 ]]; then
  echo "[hotmic] No label provided; using default: ${LABEL}" >&2
  echo "[hotmic] If you installed with a custom label, pass it here (optional)." >&2
fi

PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"

USER_GUI="gui/$(id -u)"

# Try to stop the running/registered agent regardless of plist presence
launchctl bootout "$USER_GUI"/"$LABEL" >/dev/null 2>&1 || true

if [[ -f "$PLIST_PATH" ]]; then
  rm -f "$PLIST_PATH"
  echo "Removed $PLIST_PATH"
else
  echo "No LaunchAgent plist found for label: $LABEL"
fi
