# Setotower Downloader Helper

The Setotower Downloader Helper is a silent, local companion for the Setotower Downloader website. It keeps media processing on the user's own Windows or Debian/Ubuntu device and saves completed files in `Downloads/Setotower`.

It is socket/on-demand oriented: no remote worker, LAN listener, account password, browser cookie, or credential is included. The helper listens only on loopback (`127.0.0.1:17843`), accepts requests from `https://downloader.setotower.com`, and exits after five minutes without activity.

## Linux

The hosted installer installs the helper and its systemd user socket:

```sh
curl -fsSL https://downloader.setotower.com/helper/linux/install.sh | bash
```

For APT-managed installation:

```sh
curl -fsSL https://downloader.setotower.com/apt/install-source.sh | bash
```

The Linux helper requires Python 3, FFmpeg, yt-dlp, Node.js, and systemd user services. The Cineb HLS engine is included at `engine/cineb-hls.mjs`.

## Windows

Build `windows/helper.exe` with Windows .NET Framework 4.x and PowerShell. `windows/build.ps1` collects the locally installed yt-dlp, FFmpeg, and Node binaries, adds the Cineb HLS engine, creates a verified tools archive, and compiles the helper.

The Windows helper is intentionally silent and on-demand. It registers the `setotower-helper://start` protocol for the website, does not add startup registration, and removes its idle listener after five minutes.

## Supported sources

yt-dlp handles supported public media URLs. Cineb downloads use a prepared HLS VOD `.m3u8` URL and the bundled engine. Use only content you are authorized to download and follow the terms of the source service.

## Security and privacy

The repository contains source code only. It contains no Cloudflare credentials, R2 keys, tunnel tokens, private SSH keys, production logs, browser cookies, or generated helper binaries. Runtime tokens are generated in memory per helper process. Requests are origin-checked and mutating requests also require that process token.

The Windows distributed tool archive contains third-party tools and their license/source notices; this public source repository does not redistribute those binaries.

## License

The Setotower helper source is released under the MIT License. Third-party tools remain under their own licenses.
