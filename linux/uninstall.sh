#!/usr/bin/env bash
set -euo pipefail

# Remove only the Setotower helper installation. User downloads are preserved.
BASE="$HOME/.local/share/setotower-helper"
USER_UNITS="$HOME/.config/systemd/user"

systemctl --user disable --now setotower-helper.socket 2>/dev/null || true
systemctl --user disable --now setotower-helper.service 2>/dev/null || true
systemctl --user daemon-reload 2>/dev/null || true

rm -f \
  "$USER_UNITS/setotower-helper.socket" \
  "$USER_UNITS/setotower-helper.service"

if [ -d "$BASE" ]; then
  find "$BASE" -maxdepth 2 -type f -delete
  find "$BASE" -depth -type d -empty -delete
fi

echo "Setotower helper removed. Downloads in $HOME/Downloads/Setotower were preserved."
