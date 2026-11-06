# Setotower Downloader

The downloader is the client-side download product for Setotower. The browser
site selects a format and sends the job to a helper on the same device. Video
bytes are written directly to that device's `Downloads/Setotower` folder; the
web host does not keep a media copy.

## Engines

- `yt-dlp` handles any valid HTTP(S) page or media URL that its extractor
  supports; the helper does not require a per-site allowlist.
- `aria2c` handles `magnet:?` BitTorrent links and writes the completed torrent
  contents directly to the device's `Downloads/Setotower` folder. Use magnets
  only for content you are authorized to download. Before download, the helper
  fetches the magnet metadata and shows the real file list and sizes in the
  browser, like a desktop download manager.
- Video downloads explicitly request separate best-video and best-audio
  streams, then merge them with FFmpeg so sites such as TikTok retain sound.
- Instagram Reels, Threads, and similar session-protected links use the local
  Brave browser cookie store when available. The helper never uploads those
  cookies; private or login-restricted content still cannot be downloaded.
- `engine/cineb-hls.mjs` handles a prepared, authorized Cineb HLS VOD URL. It
  fetches TS segments in bounded parallel batches and pipes them in playlist
  order to FFmpeg for a progressive MP4.
- Cineb movie/series pages are resolved automatically through the local Cineb
  bridge. The bridge asks Cineb for provider candidates, tries them headlessly,
  and caches the first working HLS stream during Fetch so Download starts
  without repeating provider discovery.

## Linux install

On Debian/Ubuntu:

```sh
curl -fsSL https://downloader.setotower.com/helper/linux/install.sh | bash
```

The socket unit listens only on `127.0.0.1:17843`. The helper process starts
when the downloader connects and exits after five idle minutes.

To uninstall the Linux helper without removing downloaded media:

```sh
curl -fsSL https://downloader.setotower.com/helper/linux/uninstall.sh | bash
```

The script removes only the helper units/runtime files and preserves
`~/Downloads/Setotower`.

## Windows install

Download `helper.exe` from the downloader site and run it once. The Windows
helper is silent, per-user, loopback-only, and saves to `Downloads\\Setotower`.
It exits after five idle minutes. When shipping the HLS engine in a new
Windows release, run `helper/build.ps1` on Windows; it rebuilds the SHA-256
tool archive hash in `BuildConfig.cs` before compiling the helper.

To uninstall the Windows helper without removing downloaded media, run:

```powershell
irm https://downloader.setotower.com/helper/uninstall.ps1 | iex
```

The script removes only `%LOCALAPPDATA%\SetotowerDownloader` and preserves
`Downloads\Setotower`.

Both helpers reject localhost, private, link-local, reserved, and multicast
HTTP targets, including hostnames that resolve to private addresses. The
helpers only accept requests from `https://downloader.setotower.com` and keep
download bytes on the client device.

## Local host

The local service file is `local-host.service`. It serves the built site on
port `3077` and can be installed for the current Linux user with:

```sh
install -d ~/.config/systemd/user
install -m 0644 downloader/local-host.service ~/.config/systemd/user/setotower-downloader-local.service
systemctl --user daemon-reload
systemctl --user enable --now setotower-downloader-local.service
```

Point the public DNS/tunnel/reverse proxy for `downloader.setotower.com` at
this machine's port `3077` to bypass Phoenix. The local service itself does
not change DNS, router forwarding, or Cloudflare settings.

If the current `cloudflared` login is for another Cloudflare zone, authenticate
once with the Setotower account, create a tunnel, then route the hostname:

```sh
cloudflared tunnel login
cloudflared tunnel create setotower-downloader-local
cloudflared tunnel route dns setotower-downloader-local downloader.setotower.com
cloudflared tunnel --config ~/.cloudflared/setotower-downloader.yml run
```

Use `local-tunnel.example.yml` as the ingress config. This must be done with
the Setotower Cloudflare account; the local machine's existing certificate is
for a different zone.
