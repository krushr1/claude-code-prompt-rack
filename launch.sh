#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="Prompt Rack"
USER_APP="$HOME/Applications/$APP_NAME.app/Contents/MacOS/$APP_NAME"
SYSTEM_APP="/Applications/$APP_NAME.app/Contents/MacOS/$APP_NAME"
LOCAL_APP="$SCRIPT_DIR/dist/$APP_NAME.app/Contents/MacOS/$APP_NAME"
RUST_BIN="$SCRIPT_DIR/target/release/prompt-rack"

if [ -x "$RUST_BIN" ]; then
  CMD=("$RUST_BIN")
elif [ -x "$USER_APP" ]; then
  CMD=("$USER_APP")
elif [ -x "$SYSTEM_APP" ]; then
  CMD=("$SYSTEM_APP")
elif [ -x "$LOCAL_APP" ]; then
  CMD=("$LOCAL_APP")
else
  CMD=(python3 "$SCRIPT_DIR/app.py")
fi

nohup "${CMD[@]}" --prompt-rack-child --auto-manager >/dev/null 2>&1 &
echo "Prompt Rack launched"
