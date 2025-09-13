#!/usr/bin/env bash
set -euo pipefail

LABEL_DEFAULT="com.hotmic"
LABEL="${1:-$LABEL_DEFAULT}"
if [[ $# -eq 0 ]]; then
  echo "[hotmic] No label provided; using default: ${LABEL}" >&2
  echo "[hotmic] Tip: pass a custom label only if you know you need it (optional)." >&2
fi

# Resolve project directory as repo root (scripts/..)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/${LABEL}.plist"
TEMPLATE="$PROJECT_DIR/launch/launchagent.plist.template"

mkdir -p "$PLIST_DIR"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "Template not found: $TEMPLATE" >&2
  exit 1
fi

# Ensure logs dir exists (launchd won't create parent dirs for stdio)
mkdir -p "$HOME/Library/Logs"

# Render template (note: launchd does NOT expand ${HOME} in paths)
sed \
  -e "s#__LABEL__#${LABEL}#g" \
  -e "s#__PROJECT_DIR__#${PROJECT_DIR//\//\/}#g" \
  -e "s#__HOME__#${HOME//\//\/}#g" \
  "$TEMPLATE" > "$PLIST_PATH"

echo "Wrote $PLIST_PATH"

# Reload agent using bootstrap flow
USER_GUI="gui/$(id -u)"
launchctl bootout "$USER_GUI"/"$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "$USER_GUI" "$PLIST_PATH"
launchctl enable "$USER_GUI"/"$LABEL"
launchctl kickstart -k "$USER_GUI"/"$LABEL"
echo "Loaded LaunchAgent: $LABEL"
echo "Logs: $HOME/Library/Logs/${LABEL}.out.log and .err.log"
echo "To uninstall: scripts/uninstall_launchagent.sh ${LABEL} (or run without args for default)"

# Help the user grant the right macOS permissions for hotkeys and auto-paste
INTERP=""
if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  INTERP="$PROJECT_DIR/.venv/bin/python"
else
  # Resolve python3 using the PATH we set in the plist
  INTERP="$(PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin command -v python3 || true)"
fi
if [[ -n "$INTERP" ]]; then
  echo
  echo "Important: grant permissions for background control"
  echo " - Accessibility: add $INTERP"
  echo " - Input Monitoring: add $INTERP"
  echo "Where: System Settings → Privacy & Security"
  echo "Reason: LaunchAgents run headless; Terminal's permission does not apply to $INTERP."
fi
