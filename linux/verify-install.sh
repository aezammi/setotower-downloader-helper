#!/usr/bin/env bash
set -euo pipefail

# Read-only post-install verification. It never enables, restarts, or removes
# units, so it is safe to run during a clean-install test or on a live system.
BASE="${SETOTOWER_HELPER_HOME:-${HOME}/.local/share/setotower-helper}"
UNITS="${SETOTOWER_HELPER_UNITS:-${HOME}/.config/systemd/user}"
SOCKET="$UNITS/setotower-helper.socket"
SERVICE="$UNITS/setotower-helper.service"

for path in "$BASE/setotower-helper.py" "$BASE/tools/cineb-hls.mjs" "$SOCKET" "$SERVICE"; do
  test -f "$path" || { echo "missing: $path" >&2; exit 1; }
done

python3 -m py_compile "$BASE/setotower-helper.py"
systemd-analyze verify "$SOCKET" "$SERVICE"
systemctl --user is-enabled setotower-helper.socket >/dev/null
systemctl --user is-active setotower-helper.socket >/dev/null

health="$(curl -fsS -H 'Origin: https://downloader.setotower.com' \
  http://127.0.0.1:17843/v1/health)"
python3 - "$health" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
if data.get("app") != "setotower-helper":
    raise SystemExit("unexpected helper identity")
if data.get("state") not in {"ready", "preparing"}:
    raise SystemExit(f"helper is not usable: {data.get('state')}")
print(f"helper state: {data['state']}")
PY

echo 'Setotower Linux helper installation verified (read-only).'
