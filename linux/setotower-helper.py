#!/usr/bin/env python3
import json, os, re, secrets, shutil, socket, socketserver, subprocess, tempfile, threading, time, urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ORIGIN = "https://downloader.setotower.com"
APP_HOME = Path(os.environ.get("SETOTOWER_HELPER_HOME", str(Path.home() / ".local/share/setotower-helper")))
TOOLS = APP_HOME / "tools"
DEST = Path.home() / "Downloads/Setotower"
TOKEN = secrets.token_urlsafe(32)
JOBS = {}
LOCK = threading.Lock()
ACTIVE = False
LAST_ACTIVITY = time.monotonic()
READY_SOURCES = {}
BROWSER_SESSION_URLS = set()
MAGNET_METADATA = {}
BRIDGE = os.environ.get("SETOTOWER_CINEB_BRIDGE", "http://127.0.0.1:8765/api/resolve")

def json_body(handler, status, value):
    raw = json.dumps(value).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", ORIGIN)
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Setotower-Token")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.end_headers()
    handler.wfile.write(raw)

def valid_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "magnet": return bool(urllib.parse.parse_qs(parsed.query).get("xt"))
    return parsed.scheme in ("http", "https") and not parsed.username and not parsed.password

def is_magnet(url):
    return urllib.parse.urlparse(url).scheme.lower() == "magnet"

def uses_browser_session(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")
    return host in {"instagram.com", "instagr.am", "threads.net"} or host.endswith(".instagram.com")

def ytdlp_binary():
    preferred = Path.home() / ".local/bin/yt-dlp"
    return str(preferred) if preferred.exists() else (shutil.which("yt-dlp") or "yt-dlp")

def bdecode(raw, index=0):
    marker = raw[index:index + 1]
    if marker == b"i":
        end = raw.index(b"e", index); return int(raw[index + 1:end]), end + 1
    if marker == b"l":
        values = []; index += 1
        while raw[index:index + 1] != b"e": value, index = bdecode(raw, index); values.append(value)
        return values, index + 1
    if marker == b"d":
        values = {}; index += 1
        while raw[index:index + 1] != b"e":
            key, index = bdecode(raw, index); value, index = bdecode(raw, index); values[key] = value
        return values, index + 1
    colon = raw.index(b":", index); length = int(raw[index:colon]); start = colon + 1
    return raw[start:start + length], start + length

def format_size(size):
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB": return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024

def inspect_torrent_file(path):
    metadata, _ = bdecode(Path(path).read_bytes())
    info = metadata.get(b"info", {})
    root = info.get(b"name", b"torrent").decode("utf-8", "replace")
    files = []
    if isinstance(info.get(b"files"), list):
        for index, item in enumerate(info[b"files"]):
            parts = [part.decode("utf-8", "replace") for part in item.get(b"path", [])]
            files.append({"index": index, "path": "/".join([root, *parts]), "size": int(item.get(b"length", 0)), "sizeLabel": format_size(item.get(b"length", 0))})
    else:
        length = int(info.get(b"length", 0)); files.append({"index": 0, "path": root, "size": length, "sizeLabel": format_size(length)})
    files.sort(key=lambda item: item["path"].lower())
    return files

def magnet_metadata(url):
    cached = MAGNET_METADATA.get(url)
    if cached and cached["expires"] > time.monotonic(): return cached["files"]
    with tempfile.TemporaryDirectory(prefix="setotower-magnet-") as folder:
        command = [
            "aria2c", "--bt-metadata-only=true", "--bt-save-metadata=true",
            "--dir", folder, "--seed-time=0", "--summary-interval=0",
            "--file-allocation=none", "--enable-dht=true", "--enable-peer-exchange=true",
            "--bt-enable-lpd=true", "--bt-tracker-connect-timeout=8",
            "--bt-tracker-timeout=8", "--connect-timeout=8", "--timeout=20",
            "--bt-stop-timeout=25", "--", url,
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=35)
        except subprocess.TimeoutExpired as exc:
            detail = (exc.stderr or "").strip().splitlines()[-1:]
            raise ValueError("Torrent metadata timed out; the magnet has no reachable peers right now.") from exc
        torrent_files = list(Path(folder).glob("*.torrent"))
        if result.returncode != 0 or not torrent_files: raise ValueError("The magnet metadata could not be fetched. The torrent may have no reachable peers.")
        files = inspect_torrent_file(torrent_files[0])
    MAGNET_METADATA[url] = {"files": files, "expires": time.monotonic() + 900}
    return files

def magnet_info(url):
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    name = query.get("dn", [""])[0].strip() or "Magnet download"
    files = magnet_metadata(url)
    return {"title": name, "thumbnail": None, "uploader": "BitTorrent", "duration": None, "engine": "magnet", "video": [{"value": "torrent", "label": "Torrent files"}], "audio": [], "files": files, "totalSize": format_size(sum(item["size"] for item in files))}

def ytdlp_info(url):
    if not valid_url(url): raise ValueError("Paste a valid media link.")
    node = shutil.which("node") or "/usr/bin/node"
    base = [ytdlp_binary(), "--js-runtimes", f"node:{node}", "--extractor-args", "youtube:player_client=android", "--dump-single-json", "--skip-download", "--no-playlist"]
    attempts = [base]
    if uses_browser_session(url): attempts.append(base + ["--cookies-from-browser", "brave"])
    result = None
    for command in attempts:
        candidate = subprocess.run(command + [url], capture_output=True, text=True, timeout=120)
        result = candidate
        if candidate.returncode == 0:
            if len(attempts) > 1 and command is attempts[1]: BROWSER_SESSION_URLS.add(url)
            break
    if result.returncode:
        reason = result.stderr.lower()
        if "unavailable" in reason: raise ValueError("This video is unavailable from the source.")
        if "not a bot" in reason: raise ValueError("The source requested verification. Please retry later.")
        if uses_browser_session(url) and any(text in reason for text in ("login required", "requested content", "checkpoint", "challenge", "empty media response")):
            raise ValueError("Instagram returned no public media for this Reel (checkpoint or rate limit). It is not necessarily private; retry later or use an accessible browser session.")
        raise ValueError("The video source could not be inspected.")
    data = json.loads(result.stdout)
    heights = sorted({int(f["height"]) for f in data.get("formats", []) if f.get("height") and f.get("vcodec") not in (None, "none") and f.get("ext") == "mp4"}, reverse=True)
    return {"title": data.get("title", "Download"), "thumbnail": data.get("thumbnail"), "uploader": data.get("uploader"), "duration": data.get("duration_string"), "video": [{"value": str(h), "label": f"{h}p MP4"} for h in heights], "audio": [{"value": "best", "label": "Best available MP3"}]}

def hls_info(url):
    name = Path(urllib.parse.urlparse(url).path).stem or "Cineb video"
    return {"title": name, "thumbnail": None, "uploader": "Cineb", "duration": None, "engine": "cineb-hls",
            "video": [{"value": "best", "label": "Highest available MP4"}],
            "audio": [{"value": "best", "label": "Audio embedded in video"}]}

def is_cineb_page(url):
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return host == "cineb.setotower.com" and ("/movie/" in parsed.path.lower() or "/series/" in parsed.path.lower())

def resolve_cineb(url, data=None):
    data = data or {}
    request_data = {"url": url, "title": data.get("title", ""), "type": data.get("media_type", data.get("type", "")), "season": data.get("season"), "episode": data.get("episode")}
    request = urllib.request.Request(BRIDGE, data=json.dumps(request_data).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode())
    except Exception as error:
        raise ValueError("Cineb could not prepare a provider stream. Try again or choose another source.") from error
    payload = result.get("payload") if isinstance(result, dict) else None
    if not isinstance(payload, dict) or not payload.get("url"):
        raise ValueError("Cineb did not return a playable provider stream.")
    READY_SOURCES[url] = {"payload": payload, "expires": time.monotonic() + 900}
    return payload

def cineb_info(url, data=None):
    payload = resolve_cineb(url, data)
    title = payload.get("displayTitle") or payload.get("title") or payload.get("filename", "Cineb download").rsplit(".", 1)[0]
    resolutions = payload.get("videoResolutions") or [{"value": "best", "label": "Highest available MP4"}]
    return {"title": title, "thumbnail": payload.get("thumbnail"), "poster": bool(payload.get("thumbnail")), "uploader": "Cineb", "duration": None, "engine": "cineb-hls", "video": resolutions, "audio": [{"value": "best", "label": "Audio embedded in video"}]}

def run_job(job, url, kind, quality, headers):
    global ACTIVE
    try:
        DEST.mkdir(parents=True, exist_ok=True)
        title = "setotower-download"
        output = DEST / (title + (".mp3" if kind == "mp3" else ".mp4"))
        if is_magnet(url):
            started = time.time()
            cmd = ["aria2c", "--dir", str(DEST), "--seed-time=0", "--bt-stop-timeout=60", "--file-allocation=none", "--summary-interval=1", "--console-log-level=notice", "--download-result=hide", "--auto-file-renaming=false", "--allow-overwrite=false", "--", url]
            selected = set(job.get("request", {}).get("files") or [])
            if selected:
                metadata_files = magnet_metadata(url)
                indices = [str(item["index"] + 1) for item in metadata_files if item["path"] in selected]
                if not indices: raise ValueError("Choose at least one torrent file to download.")
                cmd[-1:-1] = ["--select-file", ",".join(indices)]
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in process.stdout:
                match = re.search(r"\((\d+)%\)", line)
                if match:
                    job["percent"] = int(match.group(1)); job["current"] = job["percent"]; job["total"] = 100
            code = process.wait()
            if code != 0: raise RuntimeError("The torrent could not be downloaded. It may have no active peers.")
            files = [path for path in DEST.rglob("*") if path.is_file() and not path.name.endswith(".aria2") and path.stat().st_mtime >= started]
            if files: job["file"] = str(files[0])
            job.update(state="complete", current=100, total=100, percent=100)
            return
        prepared = READY_SOURCES.get(url, {})
        if prepared.get("expires", 0) < time.monotonic(): prepared = {}
        payload = prepared.get("payload", {})
        source_url = payload.get("url", url)
        source_headers = payload.get("requestHeaders", headers) or headers
        if is_cineb_page(url) and not payload:
            payload = resolve_cineb(url, job.get("request", {})); source_url = payload["url"]; source_headers = payload.get("requestHeaders", {})
        if source_url.lower().split("?", 1)[0].endswith(".m3u8"):
            cmd = ["node", str(TOOLS / "cineb-hls.mjs"), "--url", source_url, "--output", str(output), "--headers-json", json.dumps(source_headers), "--ffmpeg", "ffmpeg", "--connections", "8"]
            if quality.isdigit(): cmd += ["--max-height", quality]
        else:
            fmt = "bestaudio/best" if kind == "mp3" else (f"bv*[height<={quality}]+ba/b[height<={quality}]/b" if quality.isdigit() else "bv*+ba/b")
            cmd = [ytdlp_binary(), "--extractor-args", "youtube:player_client=android", "--no-playlist", "--newline", "--progress", "--print", "after_move:SETOTOWER_FILE:%(filepath)s", "-o", str(DEST / "%(title).150B [%(id)s].%(ext)s")]
            if kind == "mp3": cmd += ["-x", "--audio-format", "mp3"]
            else: cmd += ["-f", fmt, "--merge-output-format", "mp4"]
            if url in BROWSER_SESSION_URLS: cmd += ["--cookies-from-browser", "brave"]
            cmd += ["--", url]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in process.stdout:
            line = line.strip()
            if line.startswith("SETOTOWER_FILE:"): job["file"] = line.split(":", 1)[1]
            elif line.startswith("SETOTOWER_HLS:"):
                parts = line.split(":")
                if len(parts) > 1 and parts[1].isdigit(): job["total"] = int(parts[1]); job["current"] = 0; job["percent"] = 0
            elif line.startswith("SETOTOWER_PROGRESS:"):
                match = re.search(r"(\d+)\s*/\s*(\d+)", line)
                if match:
                    job["current"] = int(match.group(1)); job["total"] = int(match.group(2)); job["percent"] = round(job["current"] * 100 / job["total"]) if job["total"] else 0
            elif line:
                match = re.search(r"(\d+(?:\.\d+)?)%", line)
                if match:
                    job["percent"] = min(100, max(0, round(float(match.group(1))))); job["current"] = job["percent"]; job["total"] = 100
        code = process.wait()
        if code != 0: raise RuntimeError("The local download engine could not complete the file.")
        if source_url.lower().split("?", 1)[0].endswith(".m3u8") and not job.get("file"):
            candidate = DEST / "setotower-download.mp4"
            if candidate.exists(): job["file"] = str(candidate)
        if not job.get("file") or not Path(job["file"]).exists() or Path(job["file"]).stat().st_size == 0:
            raise RuntimeError("The local download engine completed without producing a file.")
        job.update(state="complete", current=job.get("total", 100), total=job.get("total", 100), percent=100)
    except Exception as error:
        job.update(state="error", error=str(error))
    finally:
        ACTIVE = False

class Handler(BaseHTTPRequestHandler):
    def _allowed(self):
        return self.headers.get("Host", "").split(":", 1)[0] in ("127.0.0.1", "localhost") and self.headers.get("Origin") == ORIGIN
    def do_OPTIONS(self): json_body(self, 204, {"ok": True})
    def do_GET(self):
        global LAST_ACTIVITY
        LAST_ACTIVITY = time.monotonic()
        parsed = urllib.parse.urlparse(self.path)
        if not self._allowed(): return json_body(self, 403, {"error": "Only downloader.setotower.com may use this helper."})
        if parsed.path == "/v1/health": return json_body(self, 200, {"app": "setotower-helper", "version": "linux-1.0.3", "state": "ready", "token": TOKEN})
        if not self.headers.get("X-Setotower-Token") == TOKEN: return json_body(self, 403, {"error": "Reconnect the helper from the website."})
        if parsed.path.startswith("/v1/jobs/"):
            job = JOBS.get(parsed.path.rsplit("/", 1)[-1])
            return json_body(self, 200 if job else 404, job or {"error": "Download not found."})
        return json_body(self, 404, {"error": "Unknown endpoint."})
    def do_POST(self):
        global ACTIVE, LAST_ACTIVITY
        LAST_ACTIVITY = time.monotonic()
        if not self._allowed() or self.headers.get("X-Setotower-Token") != TOKEN: return json_body(self, 403, {"error": "Reconnect the helper from the website."})
        if ACTIVE: return json_body(self, 429, {"error": "Another download or lookup is running."})
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 16384: return json_body(self, 400, {"error": "Invalid request size."})
        data = json.loads(self.rfile.read(length))
        url = str(data.get("url", "")); kind = str(data.get("type", "mp4")); quality = str(data.get("quality", "best"))
        if not valid_url(url): return json_body(self, 400, {"error": "Invalid media URL."})
        if self.path == "/v1/info":
            try:
                if is_magnet(url): return json_body(self, 200, magnet_info(url))
                if is_cineb_page(url): return json_body(self, 200, cineb_info(url, data))
                return json_body(self, 200, hls_info(url) if url.lower().split("?", 1)[0].endswith(".m3u8") else ytdlp_info(url))
            except ValueError as error: return json_body(self, 422, {"error": str(error)})
        if self.path != "/v1/download": return json_body(self, 404, {"error": "Unknown endpoint."})
        ACTIVE = True
        job = {"id": secrets.token_hex(16), "state": "downloading", "current": 0, "total": 0, "percent": 0, "file": "", "error": ""}
        JOBS[job["id"]] = job
        job["request"] = data
        threading.Thread(target=run_job, args=(job, url, kind, quality, data.get("headers", {})), daemon=True).start()
        json_body(self, 202, job)
    def log_message(self, *_): pass

class ActivatedServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    def __init__(self, address, handler):
        if os.environ.get("LISTEN_FDS") == "1":
            socketserver.BaseServer.__init__(self, address, handler)
            self.socket = socket.fromfd(3, socket.AF_INET, socket.SOCK_STREAM)
            self.server_address = self.socket.getsockname()
            self.timeout = None
        else: super().__init__(address, handler)

def main():
    APP_HOME.mkdir(parents=True, exist_ok=True); TOOLS.mkdir(parents=True, exist_ok=True)
    server = ActivatedServer(("127.0.0.1", 17843), Handler)
    threading.Thread(target=lambda: idle_exit(server), daemon=True).start()
    server.serve_forever()

def idle_exit(server):
    while True:
        time.sleep(30)
        if not ACTIVE and time.monotonic() - LAST_ACTIVITY > 300:
            server.shutdown(); return

if __name__ == "__main__": main()
