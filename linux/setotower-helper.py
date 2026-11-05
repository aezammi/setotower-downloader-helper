#!/usr/bin/env python3
import json, os, secrets, shutil, socket, socketserver, subprocess, threading, time, urllib.parse
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
    return parsed.scheme in ("http", "https") and not parsed.username and not parsed.password

def ytdlp_info(url):
    if not valid_url(url): raise ValueError("Paste a valid media link.")
    node = shutil.which("node") or "/usr/bin/node"
    result = subprocess.run(["yt-dlp", "--js-runtimes", f"node:{node}", "--extractor-args", "youtube:player_client=android", "--dump-single-json", "--skip-download", "--no-playlist", url], capture_output=True, text=True, timeout=120)
    if result.returncode:
        reason = result.stderr.lower()
        if "unavailable" in reason: raise ValueError("This video is unavailable from the source.")
        if "not a bot" in reason: raise ValueError("The source requested verification. Please retry later.")
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
    request_data = {"url": url, "title": data.get("title", "Cineb download"), "type": data.get("media_type", data.get("type", "movie")), "season": data.get("season", 1), "episode": data.get("episode", 1)}
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
    title = payload.get("filename", "Cineb download").rsplit(".", 1)[0]
    return {"title": title, "thumbnail": None, "uploader": "Cineb", "duration": None, "engine": "cineb-hls", "video": [{"value": "best", "label": "Highest available MP4"}], "audio": [{"value": "best", "label": "Audio embedded in video"}]}

def run_job(job, url, kind, quality, headers):
    global ACTIVE
    try:
        DEST.mkdir(parents=True, exist_ok=True)
        title = "setotower-download"
        output = DEST / (title + (".mp3" if kind == "mp3" else ".mp4"))
        prepared = READY_SOURCES.get(url, {})
        if prepared.get("expires", 0) < time.monotonic(): prepared = {}
        payload = prepared.get("payload", {})
        source_url = payload.get("url", url)
        source_headers = payload.get("requestHeaders", headers) or headers
        if is_cineb_page(url) and not payload:
            payload = resolve_cineb(url, job.get("request", {})); source_url = payload["url"]; source_headers = payload.get("requestHeaders", {})
        if source_url.lower().split("?", 1)[0].endswith(".m3u8"):
            cmd = ["node", str(TOOLS / "cineb-hls.mjs"), "--url", source_url, "--output", str(output), "--headers-json", json.dumps(source_headers), "--ffmpeg", "ffmpeg", "--connections", "8"]
        else:
            fmt = "bestaudio/best" if kind == "mp3" else (f"best[height<={quality}]/best" if quality.isdigit() else "best")
            cmd = ["yt-dlp", "--extractor-args", "youtube:player_client=android", "--no-playlist", "--newline", "--progress", "--print", "after_move:SETOTOWER_FILE:%(filepath)s", "-o", str(DEST / "%(title).150B [%(id)s].%(ext)s")]
            if kind == "mp3": cmd += ["-x", "--audio-format", "mp3"]
            else: cmd += ["-f", fmt, "--merge-output-format", "mp4"]
            cmd += ["--", url]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in process.stdout:
            line = line.strip()
            if line.startswith("SETOTOWER_FILE:"): job["file"] = line.split(":", 1)[1]
            elif line: job["progress"] = line[-500:]
        code = process.wait()
        if code != 0: raise RuntimeError("The local download engine could not complete the file.")
        if source_url.lower().split("?", 1)[0].endswith(".m3u8") and not job.get("file"):
            candidate = DEST / "setotower-download.mp4"
            if candidate.exists(): job["file"] = str(candidate)
        if not job.get("file") or not Path(job["file"]).exists() or Path(job["file"]).stat().st_size == 0:
            raise RuntimeError("The local download engine completed without producing a file.")
        job.update(state="complete", progress="Download complete")
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
        if parsed.path == "/v1/health": return json_body(self, 200, {"app": "setotower-helper", "version": "linux-1.0.0", "state": "ready", "token": TOKEN})
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
                if is_cineb_page(url): return json_body(self, 200, cineb_info(url, data))
                return json_body(self, 200, hls_info(url) if url.lower().split("?", 1)[0].endswith(".m3u8") else ytdlp_info(url))
            except ValueError as error: return json_body(self, 422, {"error": str(error)})
        if self.path != "/v1/download": return json_body(self, 404, {"error": "Unknown endpoint."})
        ACTIVE = True
        job = {"id": secrets.token_hex(16), "state": "downloading", "progress": "Starting download…", "file": "", "error": ""}
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
