#!/usr/bin/env python3
import ipaddress, json, os, re, secrets, shutil, socket, socketserver, subprocess, tempfile, threading, time, urllib.parse
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
MAX_JOBS = 32
JOB_TTL = 3600
PROCESSES = {}

def probe_media(path):
    """Return stream types and duration for a completed media file."""
    probe = shutil.which("ffprobe") or "ffprobe"
    command = [probe, "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ValueError("The downloaded file is not a valid media container.")
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("The media validator returned invalid data.") from error
    streams = {item.get("codec_type") for item in data.get("streams", []) if item.get("codec_type")}
    duration = data.get("format", {}).get("duration")
    return {"streams": sorted(streams), "duration": float(duration) if duration else None}

def validate_completed_file(path, kind):
    candidate = Path(path)
    if not candidate.is_file() or candidate.stat().st_size == 0:
        raise ValueError("The local download engine completed without producing a file.")
    if candidate.suffix.lower() in {".m3u8", ".html", ".htm", ".json"}:
        raise ValueError("The provider returned a playlist or webpage instead of a media file.")
    media = probe_media(candidate)
    streams = set(media["streams"])
    required = "audio" if kind == "mp3" else "video"
    if required not in streams:
        raise ValueError("The downloaded file does not contain the requested media stream.")
    return media

def cleanup_partial_outputs(started_at):
    """Remove only fresh temporary artifacts left by a failed job."""
    if not DEST.exists(): return
    for candidate in DEST.rglob("*"):
        if not candidate.is_file() or candidate.stat().st_mtime < started_at: continue
        if candidate.name.endswith((".part", ".ytdl", ".aria2")) or candidate.name == "setotower-download.mp4":
            try: candidate.unlink()
            except OSError: pass

def is_active():
    with LOCK:
        return ACTIVE

def claim_active():
    global ACTIVE
    with LOCK:
        if ACTIVE:
            return False
        ACTIVE = True
        return True

def release_active():
    global ACTIVE
    with LOCK:
        ACTIVE = False

def public_job(job):
    """Return only safe, client-facing job fields."""
    result = {key: job.get(key) for key in ("id", "state", "current", "total", "percent", "error", "media")}
    path = job.get("file")
    result["file"] = Path(path).name if path else ""
    return result

def prune_jobs():
    cutoff = time.time() - JOB_TTL
    with LOCK:
        stale = [key for key, item in JOBS.items() if item.get("finished_at", 0) and item["finished_at"] < cutoff]
        for key in stale: JOBS.pop(key, None)

def cancel_job(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("state") in ("complete", "error", "cancelled"):
            return None
        job["cancel_requested"] = True
        process = PROCESSES.get(job_id)
    if process and process.poll() is None:
        try: process.terminate()
        except OSError: pass
    return job

def cancellation_requested(job_id):
    with LOCK:
        return bool(JOBS.get(job_id, {}).get("cancel_requested"))

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
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password: return False
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"): return False
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]
        except socket.gaierror:
            addresses = []
    return not addresses or all(address.is_global for address in addresses)

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
    started_at = time.time()
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
            with LOCK: PROCESSES[job["id"]] = process
            for line in process.stdout:
                if cancellation_requested(job["id"]):
                    process.terminate()
                    break
                match = re.search(r"\((\d+)%\)", line)
                if match:
                    job["percent"] = int(match.group(1)); job["current"] = job["percent"]; job["total"] = 100
            code = process.wait()
            if cancellation_requested(job["id"]):
                job.update(state="cancelled", error="Download cancelled.")
                return
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
        with LOCK: PROCESSES[job["id"]] = process
        for line in process.stdout:
            if cancellation_requested(job["id"]):
                process.terminate()
                break
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
        if cancellation_requested(job["id"]):
            job.update(state="cancelled", error="Download cancelled.")
            return
        if code != 0: raise RuntimeError("The local download engine could not complete the file.")
        if source_url.lower().split("?", 1)[0].endswith(".m3u8") and not job.get("file"):
            candidate = DEST / "setotower-download.mp4"
            if candidate.exists(): job["file"] = str(candidate)
        media = validate_completed_file(job.get("file", ""), kind)
        job["media"] = media
        job.update(state="complete", current=job.get("total", 100), total=job.get("total", 100), percent=100)
    except Exception as error:
        job.update(state="error", error=str(error))
        cleanup_partial_outputs(started_at)
    finally:
        if job.get("state") == "cancelled": cleanup_partial_outputs(started_at)
        with LOCK:
            PROCESSES.pop(job.get("id"), None)
            if job.get("state") in ("complete", "error", "cancelled"): job["finished_at"] = time.time()
        release_active()

class Handler(BaseHTTPRequestHandler):
    def _allowed(self):
        return self.headers.get("Host", "").split(":", 1)[0] in ("127.0.0.1", "localhost") and self.headers.get("Origin") == ORIGIN
    def do_OPTIONS(self): json_body(self, 204, {"ok": True})
    def do_GET(self):
        global LAST_ACTIVITY
        LAST_ACTIVITY = time.monotonic()
        parsed = urllib.parse.urlparse(self.path)
        if not self._allowed(): return json_body(self, 403, {"error": "Only downloader.setotower.com may use this helper."})
        if parsed.path == "/v1/health": return json_body(self, 200, {"app": "setotower-helper", "version": "linux-1.0.7", "state": "ready", "token": TOKEN})
        if not self.headers.get("X-Setotower-Token") == TOKEN: return json_body(self, 403, {"error": "Reconnect the helper from the website."})
        if parsed.path.startswith("/v1/jobs/"):
            with LOCK:
                job = JOBS.get(parsed.path.rsplit("/", 1)[-1])
            return json_body(self, 200 if job else 404, public_job(job) if job else {"error": "Download not found."})
        return json_body(self, 404, {"error": "Unknown endpoint."})
    def do_POST(self):
        global LAST_ACTIVITY
        LAST_ACTIVITY = time.monotonic()
        if not self._allowed() or self.headers.get("X-Setotower-Token") != TOKEN: return json_body(self, 403, {"error": "Reconnect the helper from the website."})
        if self.path.startswith("/v1/jobs/") and self.path.endswith("/cancel"):
            job = cancel_job(self.path.split("/")[-2])
            return json_body(self, 200 if job else 404, public_job(job) if job else {"error": "Download not found or already finished."})
        if is_active(): return json_body(self, 429, {"error": "Another download or lookup is running."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return json_body(self, 400, {"error": "Invalid request size."})
        if length < 1 or length > 16384: return json_body(self, 400, {"error": "Invalid request size."})
        try:
            data = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return json_body(self, 400, {"error": "Request body must be valid JSON."})
        if not isinstance(data, dict): return json_body(self, 400, {"error": "Request body must be a JSON object."})
        url = str(data.get("url", "")); kind = str(data.get("type", "mp4")); quality = str(data.get("quality", "best"))
        if not valid_url(url): return json_body(self, 400, {"error": "Invalid media URL."})
        if self.path == "/v1/info":
            try:
                if is_magnet(url): return json_body(self, 200, magnet_info(url))
                if is_cineb_page(url): return json_body(self, 200, cineb_info(url, data))
                return json_body(self, 200, hls_info(url) if url.lower().split("?", 1)[0].endswith(".m3u8") else ytdlp_info(url))
            except ValueError as error: return json_body(self, 422, {"error": str(error)})
        if self.path != "/v1/download": return json_body(self, 404, {"error": "Unknown endpoint."})
        if not claim_active(): return json_body(self, 429, {"error": "Another download or lookup is running."})
        job = {"id": secrets.token_hex(16), "state": "downloading", "current": 0, "total": 0, "percent": 0, "file": "", "error": ""}
        with LOCK:
            JOBS[job["id"]] = job
            finished = [key for key, item in JOBS.items() if item.get("state") in ("complete", "error", "cancelled") and key != job["id"]]
            for key in finished[:max(0, len(JOBS) - MAX_JOBS)]:
                JOBS.pop(key, None)
        job["request"] = data
        threading.Thread(target=run_job, args=(job, url, kind, quality, data.get("headers", {})), daemon=True).start()
        json_body(self, 202, public_job(job))
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
        prune_jobs()
        if not is_active() and time.monotonic() - LAST_ACTIVITY > 300:
            server.shutdown(); return

if __name__ == "__main__": main()
