#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/prashamsheth/Documents/ChatGPT/VGNA Tournament bot"
LABEL="com.vgna.tournament-bot"
SOURCE="$PROJECT_DIR/deploy/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/runtime"
cp "$SOURCE" "$TARGET"
chmod 644 "$TARGET"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "Installed and started $LABEL"
echo "Logs: $PROJECT_DIR/runtime/bot.log"
