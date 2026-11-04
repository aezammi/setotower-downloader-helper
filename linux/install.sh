#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${SETOTOWER_HELPER_BASE:-https://downloader.setotower.com/helper/linux}"
BASE="${HOME}/.local/share/setotower-helper"
mkdir -p "$BASE/tools" "$HOME/.config/systemd/user"

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y curl python3 ffmpeg yt-dlp nodejs
else
  for tool in curl python3 ffmpeg yt-dlp node; do
    command -v "$tool" >/dev/null || { echo "$tool is required" >&2; exit 1; }
  done
fi

fetch() { curl -fsSL --retry 3 "$BASE_URL/$1" -o "$2"; }
fetch setotower-helper.py "$BASE/setotower-helper.py"
fetch cineb-hls.mjs "$BASE/tools/cineb-hls.mjs"
fetch setotower-helper.socket "$HOME/.config/systemd/user/setotower-helper.socket"
fetch setotower-helper.service "$HOME/.config/systemd/user/setotower-helper.service"
chmod 0755 "$BASE/setotower-helper.py"
systemctl --user daemon-reload
systemctl --user enable --now setotower-helper.socket
echo "Setotower helper installed. It is idle until downloader.setotower.com connects."
