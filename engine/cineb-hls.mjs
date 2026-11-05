#!/usr/bin/env node
// Setotower local HLS engine: authorized VOD streams only.
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { once } from "node:events";

const argv = process.argv.slice(2);
const value = name => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : ""; };
const sourceUrl = value("--url");
const output = value("--output");
const ffmpeg = value("--ffmpeg") || "ffmpeg";
const headers = JSON.parse(value("--headers-json") || "{}");
const maxHeight = Number(value("--max-height") || 0);
const concurrency = Math.max(2, Math.min(12, Number(value("--connections") || 8)));
if (!sourceUrl || !output) { console.error("Usage: cineb-hls.mjs --url URL --output FILE [--headers-json JSON] [--ffmpeg PATH]"); process.exit(2); }

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function request(url, init = {}) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch(url, { ...init, signal: AbortSignal.timeout(30000) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response;
    } catch (error) {
      if (attempt === 2) throw error;
      await sleep(500 * (attempt + 1));
    }
  }
}
const playlistUrls = (text, base) => text.split(/\r?\n/).map(line => line.trim()).filter(line => line && !line.startsWith("#")).map(line => new URL(line, base).href);
function highestVariant(text, base) {
  const lines = text.split(/\r?\n/).map(line => line.trim());
  const variants = [];
  for (let i = 0; i < lines.length; i += 1) {
    if (!lines[i].startsWith("#EXT-X-STREAM-INF:")) continue;
    const bandwidth = Number(lines[i].match(/(?:AVERAGE-)?BANDWIDTH=(\d+)/)?.[1] || 0);
    const height = Number(lines[i].match(/RESOLUTION=\d+x(\d+)/)?.[1] || 0);
    const uri = lines.slice(i + 1).find(line => line && !line.startsWith("#"));
    if (uri) variants.push({ bandwidth, height, url: new URL(uri, base).href });
  }
  const eligible = maxHeight ? variants.filter(item => !item.height || item.height <= maxHeight) : variants;
  return (eligible.length ? eligible : variants).sort((a, b) => b.bandwidth - a.bandwidth)[0]?.url || "";
}
async function discover() {
  let playlist = sourceUrl;
  let response = await request(playlist, { headers });
  let text = await response.text();
  const variant = highestVariant(text, playlist);
  if (variant) { playlist = variant; response = await request(playlist, { headers }); text = await response.text(); }
  if (!/#EXT-X-ENDLIST\b/.test(text)) throw new Error("The HLS source is not a completed VOD playlist.");
  if (/#EXT-X-KEY:.*METHOD=(?!NONE)/.test(text)) throw new Error("Encrypted HLS sources are not supported by this local engine.");
  const segments = playlistUrls(text, playlist);
  if (!segments.length || segments.some(url => !/\.(?:ts|mpegts)(?:[?#]|$)/i.test(url))) throw new Error("Unsupported HLS segment format.");
  return segments;
}
async function feed(segments, input) {
  let scheduled = 0;
  const pending = new Map();
  const schedule = () => {
    while (scheduled < segments.length && pending.size < concurrency) {
      const index = scheduled++;
      pending.set(index, request(segments[index], { headers }).then(response => response.arrayBuffer()).then(Buffer.from));
    }
  };
  schedule();
  for (let i = 0; i < segments.length; i += 1) {
    const chunk = await pending.get(i);
    pending.delete(i);
    schedule();
    if (!input.write(chunk)) await once(input, "drain");
    process.stdout.write(`SETOTOWER_PROGRESS:${i + 1}/${segments.length}\n`);
  }
  input.end();
}
async function main() {
  const part = `${output}.part`;
  await fsp.rm(part, { force: true });
  const segments = await discover();
  process.stdout.write(`SETOTOWER_HLS:${segments.length}:${concurrency}\n`);
  const child = spawn(ffmpeg, ["-hide_banner", "-loglevel", "error", "-nostdin", "-f", "mpegts", "-i", "pipe:0", "-map", "0:v:0?", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"], { stdio: ["pipe", "pipe", "pipe"] });
  let errorText = "";
  child.stderr.on("data", chunk => { errorText += String(chunk); if (errorText.length > 4000) errorText = errorText.slice(-4000); });
  const file = fs.createWriteStream(part, { flags: "wx" });
  child.stdout.pipe(file);
  const close = once(child, "close").then(([code]) => code);
  const feedTask = feed(segments, child.stdin).catch(error => { child.kill("SIGKILL"); throw error; });
  const [code] = await Promise.all([close, feedTask]);
  await once(file, "close");
  if (code !== 0) throw new Error(`FFmpeg failed: ${errorText.trim() || code}`);
  const stat = await fsp.stat(part);
  if (!stat.size) throw new Error("The HLS engine produced an empty file.");
  await fsp.mkdir(path.dirname(output), { recursive: true });
  await fsp.rename(part, output);
  process.stdout.write(`SETOTOWER_FILE:${output}\n`);
}
main().catch(async error => { await fsp.rm(`${output}.part`, { force: true }).catch(() => {}); console.error(error instanceof Error ? error.message : String(error)); process.exit(1); });
