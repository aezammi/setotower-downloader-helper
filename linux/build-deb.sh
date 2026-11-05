#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-1.0.0}"
WORK="$(mktemp -d)"
PKG="$WORK/setotower-helper_${VERSION}_all"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$PKG/DEBIAN" "$PKG/usr/lib/setotower-helper/tools" "$PKG/usr/lib/systemd/user"
install -m 0755 "$ROOT/linux/setotower-helper.py" "$PKG/usr/lib/setotower-helper/setotower-helper.py"
install -m 0755 "$ROOT/engine/cineb-hls.mjs" "$PKG/usr/lib/setotower-helper/tools/cineb-hls.mjs"
install -m 0644 "$ROOT/linux/setotower-helper.socket" "$PKG/usr/lib/systemd/user/setotower-helper.socket"
cat > "$PKG/usr/lib/systemd/user/setotower-helper.service" <<'UNIT'
[Unit]
Description=Setotower Downloader Helper
Requires=setotower-helper.socket
After=setotower-helper.socket

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/lib/setotower-helper/setotower-helper.py
Environment=SETOTOWER_HELPER_HOME=/usr/lib/setotower-helper
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=/usr/lib/setotower-helper
NoNewPrivileges=true
PrivateTmp=true
Restart=no
StandardOutput=journal
StandardError=journal
UNIT
cat > "$PKG/DEBIAN/control" <<CONTROL
Package: setotower-helper
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Maintainer: Setotower <niroulaoskar@gmail.com>
Depends: python3 (>= 3.10), ffmpeg, yt-dlp, nodejs, aria2
Description: Setotower silent on-demand download helper
 Downloads supported media directly to the local user's Downloads/Setotower folder.
 Includes the Cineb HLS engine and uses systemd socket activation.
CONTROL
dpkg-deb --build --root-owner-group "$PKG" "$WORK/setotower-helper_${VERSION}_all.deb" >/dev/null

mkdir -p "$ROOT/apt/pool" "$ROOT/apt/dists/stable/main/binary-all"
install -m 0644 "$WORK/setotower-helper_${VERSION}_all.deb" "$ROOT/apt/pool/"
(cd "$ROOT/apt" && dpkg-scanpackages pool /dev/null > dists/stable/main/binary-all/Packages 2>/dev/null)
# Keep the compressed index byte-for-byte reproducible so CDN caching cannot
# create a Release/Packages.gz hash mismatch when the package content is the same.
gzip -n -9c "$ROOT/apt/dists/stable/main/binary-all/Packages" > "$ROOT/apt/dists/stable/main/binary-all/Packages.gz"
release="$ROOT/apt/dists/stable/Release"
packages="$ROOT/apt/dists/stable/main/binary-all/Packages"
packages_gz="$ROOT/apt/dists/stable/main/binary-all/Packages.gz"
{
  echo 'Origin: Setotower'
  echo 'Label: Setotower Downloader'
  echo 'Suite: stable'
  echo 'Codename: stable'
  echo "Date: $(date -Ru)"
  echo 'Architectures: all'
  echo 'Components: main'
  echo 'MD5Sum:'
  printf ' %s %s main/binary-all/Packages\n' "$(md5sum "$packages" | cut -d' ' -f1)" "$(stat -c '%s' "$packages")"
  printf ' %s %s main/binary-all/Packages.gz\n' "$(md5sum "$packages_gz" | cut -d' ' -f1)" "$(stat -c '%s' "$packages_gz")"
  echo 'SHA256:'
  printf ' %s %s main/binary-all/Packages\n' "$(sha256sum "$packages" | cut -d' ' -f1)" "$(stat -c '%s' "$packages")"
  printf ' %s %s main/binary-all/Packages.gz\n' "$(sha256sum "$packages_gz" | cut -d' ' -f1)" "$(stat -c '%s' "$packages_gz")"
} > "$release"
echo "Built setotower-helper ${VERSION} and refreshed $ROOT/apt"
