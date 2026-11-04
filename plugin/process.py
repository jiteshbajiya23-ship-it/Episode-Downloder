"""
Website link -> Telegram (+ stream/download links)

Send a xiaobhaidonghua.com EPISODE link to the bot (admins only):
  1. page is read, the Dailymotion/Rumble/YouTube source is found (yt-dlp)
  2. you tick the qualities (all ticked by default) and press START
  3. English subtitle is downloaded and sent first
  4. qualities are processed ONE AT A TIME in ASCENDING order: download -> English subtitle
     soft-mux + metadata (stream copy, no re-encode) -> upload -> delete the file from disk
  5. each video lands in LOG_CHANNEL, is copied to your chat and gets
     Stream / Download buttons

Optional env variables:
  DOWNLOAD_DIR=/tmp/xiaobhai_dl   DOWNLOAD_ATTEMPTS=3
  DOWNLOAD_CONNECTIONS=8          ARIA2_CONNECTIONS=16    USE_ARIA2=auto|on|off
  YTDLP_PROXY=http://user:pass@host:port   (only if Dailymotion blocks the server IP)
  YTDLP_COOKIES_B64=<base64 of dailymotion cookies.txt>   (helps with HTTP 401)
"""
import asyncio
import base64
import html
import json
import logging
import re
import shutil
import subprocess
import time
import uuid
from os import environ
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs, quote

import requests
from bs4 import BeautifulSoup
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from yt_dlp import YoutubeDL
except ImportError:  # never let a missing package break the whole bot
    YoutubeDL = None

from info import ADMINS, LOG_CHANNEL, URL, SHORTLINK
from utils import get_shortlink
from xiaobhai.util.human_readable import humanbytes
from xiaobhai.util.file_properties import get_name, get_hash

log = logging.getLogger("process")

# =========================
# CONFIG
# =========================
DOWNLOAD_DIR = Path(environ.get("DOWNLOAD_DIR", "/tmp/xiaobhai_dl"))
DOWNLOAD_ATTEMPTS = max(1, int(environ.get("DOWNLOAD_ATTEMPTS", "3") or 3))
DOWNLOAD_CONNECTIONS = max(1, int(environ.get("DOWNLOAD_CONNECTIONS", "8") or 8))
ARIA2_CONNECTIONS = min(16, max(1, int(environ.get("ARIA2_CONNECTIONS", "16") or 16)))
USE_ARIA2 = environ.get("USE_ARIA2", "auto").strip().lower()
YTDLP_PROXY = environ.get("YTDLP_PROXY", "").strip()
YTDLP_COOKIES_B64 = environ.get("YTDLP_COOKIES_B64", "").strip()
COOKIE_FILE = Path("/tmp/dm_cookies.txt")
if YTDLP_COOKIES_B64:
    try:
        COOKIE_FILE.write_bytes(base64.b64decode(YTDLP_COOKIES_B64))
    except Exception:
        log.exception("Could not write cookies file")
MAX_UPLOAD_BYTES = 2000 * 1024 * 1024  # bot upload limit is ~2 GB
BRAND = "XiaoBhaiDonghua"

try:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    log.exception("Could not create DOWNLOAD_DIR")

JOBS: dict = {}
RUN_LOCK = asyncio.Lock()  # one episode at a time (small server)

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class Cancelled(Exception):
    pass


# =========================
# SMALL HELPERS
# =========================
_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, max_len: int = 120) -> str:
    name = _INVALID_FS_CHARS.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:max_len].rstrip(" .") or "episode"


def clean_url(url: str) -> str:
    return html.unescape(url).replace("\\/", "/").replace("\\u002F", "/").strip(" \t\r\n\"'<>[](){}")


def is_own_site_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host == "xiaobhaidonghua.com" or host.endswith(".xiaobhaidonghua.com")
    except Exception:
        return False


def tmpl_escape(path_like) -> str:
    return str(path_like).replace("%", "%%")


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def have_aria2() -> bool:
    return shutil.which("aria2c") is not None


def bar(fraction: float, width: int = 12) -> str:
    fraction = max(0.0, min(1.0, fraction or 0.0))
    filled = int(round(fraction * width))
    return "▰" * filled + "▱" * (width - filled)



# =========================
# WEBSITE PAGE -> SOURCE URL   (same logic as xiaobhaibot)
# =========================
DM_URL_RE = re.compile(r"(?:https?:)?//(?:www\.|geo\.)?dailymotion\.com/(?:embed/)?video/([A-Za-z0-9]+)", re.I)
DM_DAILY_RE = re.compile(r"(?:https?:)?//dai\.ly/([A-Za-z0-9]+)", re.I)
SEASON_EP_RE = re.compile(r"(?:season[-_ ]?(\d+)[-_ ]*episode[-_ ]?(\d+))|(?:s(\d+)[-_ ]*e(\d+))", re.I)
EP_ONLY_RE = re.compile(r"(?:episode[-_ ]?(\d+))|(?:\be(\d+)\b)", re.I)

SOURCE_PATTERNS = {
    "Dailymotion": re.compile(r"(?:https?:)?//(?:www\.|geo\.)?dailymotion\.com/(?:embed/)?video/[A-Za-z0-9]+|(?:https?:)?//dai\.ly/[A-Za-z0-9]+", re.I),
    "Rumble": re.compile(r"https?://(?:www\.)?rumble\.com/(?:embed/)?[A-Za-z0-9][A-Za-z0-9_-]*", re.I),
    "YouTube": re.compile(r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/|embed/)[A-Za-z0-9_-]+|youtu\.be/[A-Za-z0-9_-]+)", re.I),
}


def fetch_page(url: str):
    r = requests.get(url, headers=HTTP_HEADERS, timeout=30)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = "utf-8"
    return r.text, BeautifulSoup(r.text, "html.parser")


def dm_url_from_id(video_id: str) -> str:
    return f"https://www.dailymotion.com/video/{video_id}"


def dm_video_id_from_url(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path
        if "dailymotion.com" not in host and host != "dai.ly":
            return None
        m = re.search(r"/(?:embed/)?video/([A-Za-z0-9_-]+)", path, re.I)
        if m:
            return m.group(1)
        if re.search(r"/player/[^/]+\.html", path, re.I):
            values = parse_qs(parsed.query).get("video", [])
            if values:
                return re.match(r"[A-Za-z0-9_-]+", values[0]).group(0)
        if host == "dai.ly":
            m = re.search(r"/([A-Za-z0-9_-]+)", path)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def find_source_urls(raw_html: str, soup: BeautifulSoup) -> dict:
    found = {name: [] for name in SOURCE_PATTERNS}
    candidates = []
    for node in soup.find_all(["iframe", "a", "source", "video"]):
        for attr in ("src", "href", "data-src", "data-url", "data-player-url", "data-video-url", "content"):
            value = node.get(attr)
            if value:
                candidates.append(clean_url(str(value)))
    candidates.append(html.unescape(raw_html).replace("\\/", "/").replace("\\u002F", "/"))

    for value in candidates:
        value = html.unescape(str(value)).replace("\\/", "/").replace("\\u002F", "/")
        for pm in re.finditer(r"(?:https?:)?//(?:www\.|geo\.)?dailymotion\.com/player/[^?#]+\.html\?[^\s\"'<>]*", value, re.I):
            player_url = pm.group(0)
            if player_url.startswith("//"):
                player_url = "https:" + player_url
            vid = dm_video_id_from_url(player_url)
            if vid:
                norm = dm_url_from_id(vid)
                if norm not in found["Dailymotion"]:
                    found["Dailymotion"].append(norm)
        for m in DM_URL_RE.finditer(value):
            norm = dm_url_from_id(m.group(1))
            if norm not in found["Dailymotion"]:
                found["Dailymotion"].append(norm)
        for m in DM_DAILY_RE.finditer(value):
            norm = dm_url_from_id(m.group(1))
            if norm not in found["Dailymotion"]:
                found["Dailymotion"].append(norm)
        for name, pattern in SOURCE_PATTERNS.items():
            if name == "Dailymotion":
                continue
            for match in pattern.findall(value):
                url = clean_url(match if isinstance(match, str) else match[0])
                if url.startswith("//"):
                    url = "https:" + url
                if url not in found[name]:
                    found[name].append(url)
    return found


def remove_episode_words(title: str) -> str:
    title = re.sub(r"\bsubtitle[-_ ]*multi\b", "", title, flags=re.I)
    title = re.sub(r"\bmulti[-_ ]*subtitle\b", "", title, flags=re.I)
    title = re.sub(r"\bsubtitles?[-_ ]*multi\b", "", title, flags=re.I)
    title = re.sub(r"\bseason[-_ ]*\d+\b", "", title, flags=re.I)
    title = re.sub(r"\bs\d+\b", "", title, flags=re.I)
    title = re.sub(r"\bepisode[-_ ]*\d+\b", "", title, flags=re.I)
    title = re.sub(r"\be\d+\b", "", title, flags=re.I)
    title = re.sub(r"\b(?:english\s*)?subtitles?\b", "", title, flags=re.I)
    title = re.sub(r"\b\[?(?:4k|good sub|download link)\]?\b", "", title, flags=re.I)
    title = re.sub(r"[|:_\-\u2013\u2014]+", " ", title)
    return re.sub(r"\s+", " ", title).strip(" .-_\u2013\u2014")


def is_episode_page_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return bool(SEASON_EP_RE.search(path) or EP_ONLY_RE.search(path))


def parse_episode_meta(page_url: str, soup: BeautifulSoup):
    path = urlparse(page_url).path.lower()
    season = None
    episode = None
    m = SEASON_EP_RE.search(path)
    if m:
        season = int(m.group(1) or m.group(3))
        episode = int(m.group(2) or m.group(4))
    else:
        m = EP_ONLY_RE.search(path)
        if m:
            episode = int(m.group(1) or m.group(2))

    title = ""
    for selector in ["h1.entry-title", "h1", 'meta[property="og:title"]', "title"]:
        node = soup.select_one(selector)
        if not node:
            continue
        title = (node.get("content") or "").strip() if node.name == "meta" else node.get_text(" ", strip=True)
        if title:
            break
    title = remove_episode_words(title)
    if not title:
        title = remove_episode_words(Path(path.rstrip("/")).name.replace("-", " "))
    if episode is None:
        raise ValueError("Could not detect an episode number (this may be a series page - send a single episode link).")
    return title, season, episode


def make_base_name(title: str, season: Optional[int], episode: int) -> str:
    raw = f"S{season}E{episode} {title}" if season is not None else f"E{episode} {title}"
    return safe_filename(raw)


# =========================
# yt-dlp: FORMATS / QUALITIES   (same logic as xiaobhaibot)
# =========================
class _QuietLogger:
    """Swallow yt-dlp console output (progress lines, ERROR duplicates). Errors still raise."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): log.debug("yt-dlp: %s", msg)
    def error(self, msg): log.debug("yt-dlp: %s", msg)


class FormatUnavailable(RuntimeError):
    """The requested quality is not offered by the source (retrying will not help)."""


def ydl_opts(**extra) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _QuietLogger(),
        "noplaylist": True,
        "source_address": "0.0.0.0",  # Dailymotion works best over IPv4
    }
    if YTDLP_PROXY:
        opts["proxy"] = YTDLP_PROXY
    if COOKIE_FILE.exists():
        opts["cookiefile"] = str(COOKIE_FILE)
    opts.update(extra)
    return opts


def _imp_value(target: str):
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        return ImpersonateTarget.from_str(target)
    except Exception:
        return target


def _imp_targets(url: str, cached: Optional[str] = None) -> list:
    low = str(url).lower()
    if "dailymotion.com" not in low and "dai.ly" not in low:
        return [None]
    order = [cached] if cached else []
    for t in (None, "chrome", "firefox"):
        if t not in order:
            order.append(t)
    return order


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_audio_only(f: dict) -> bool:
    return f.get("vcodec") == "none" and f.get("acodec") != "none" and f.get("ext") != "mhtml"


def is_video(f: dict) -> bool:
    return f.get("vcodec") != "none" and f.get("ext") != "mhtml"


def quality_label(f: dict) -> Optional[int]:
    try:
        if f.get("height"):
            return int(f["height"])
    except (TypeError, ValueError):
        pass
    m = re.search(r"(?:hls|http|dash)[-_](\d{3,4})(?!\d)", str(f.get("format_id") or ""), re.I)
    return int(m.group(1)) if m else None


def video_rank(f: dict) -> tuple:
    codec = str(f.get("vcodec") or "")
    return (1 if codec.startswith(("avc", "h264")) else 0, _num(f.get("tbr")), _num(f.get("fps")))


def audio_rank(f: dict) -> tuple:
    return (
        1 if "original" in str(f.get("format_id") or "").lower() else 0,
        1 if str(f.get("acodec") or "").startswith("mp4a") else 0,
        _num(f.get("abr")) or _num(f.get("tbr")),
    )


def build_quality_map(formats: list) -> dict:
    audio = sorted((f for f in formats if is_audio_only(f) and f.get("format_id")), key=audio_rank, reverse=True)
    best_audio_id = audio[0]["format_id"] if audio else None

    grouped: dict = {}
    for f in formats:
        if not is_video(f) or not f.get("format_id"):
            continue
        label = quality_label(f)
        if label:
            grouped.setdefault(label, []).append(f)

    selectors: dict = {}
    for label, items in grouped.items():
        best = max(items, key=video_rank)
        vid = best["format_id"]
        parts = []
        if best.get("acodec") not in (None, "none"):
            parts.append(vid)
        else:
            if best_audio_id:
                parts.append(f"{vid}+{best_audio_id}")
            parts.append(f"{vid}+ba")
        parts.append(f"bv*[height={label}]+ba")
        parts.append(f"b[height={label}]")
        parts.append(f"bv*[format_id*={label}]+ba")
        parts.append(f"b[format_id*={label}]")
        selectors[label] = "/".join(parts)
    return selectors


def _english_subtitle_info(info: dict):
    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}

    def english_keys(d: dict) -> list:
        keys = []
        for key in d:
            lk = str(key).lower().replace("_", "-")
            if lk == "en" or lk.startswith("en-") or lk.startswith("eng") or lk in {"english", "en-us", "en-gb"}:
                keys.append(key)
        return keys

    manual_en = english_keys(manual)
    automatic_en = english_keys(automatic)
    if manual_en:
        return manual_en[0], False
    if automatic_en:
        return automatic_en[0], True
    return None, False


def _english_subtitle_urls(info: dict, key, auto: bool) -> list:
    src = (info.get("automatic_captions") if auto else info.get("subtitles")) or {}
    return [{"url": x["url"], "ext": x.get("ext")} for x in (src.get(key) or []) if x.get("url")]


def estimate_sizes(formats: list, duration) -> dict:
    """Rough file size per quality (video + best audio) so the buttons can show it."""
    def fsize(f: dict) -> float:
        n = _num(f.get("filesize")) or _num(f.get("filesize_approx"))
        if not n and duration and _num(f.get("tbr")):
            n = _num(f.get("tbr")) * 1000 / 8 * _num(duration)
        return n

    audio = sorted((f for f in formats if is_audio_only(f)), key=audio_rank, reverse=True)
    audio_size = fsize(audio[0]) if audio else 0
    grouped: dict = {}
    for f in formats:
        if is_video(f):
            label = quality_label(f)
            if label:
                grouped.setdefault(label, []).append(f)
    sizes = {}
    for label, items in grouped.items():
        best = max(items, key=video_rank)
        v = fsize(best)
        if v:
            has_audio = best.get("acodec") not in (None, "none")
            sizes[label] = int(v + (0 if has_audio else audio_size))
    return sizes


def inspect_source(source_url: str, source_name: str) -> dict:
    if YoutubeDL is None:
        raise RuntimeError("yt-dlp is not installed (check requirements.txt).")
    last_error = None
    attempts = [None, "chrome", "firefox"] if source_name.lower() == "dailymotion" else [None]
    for target in attempts:
        opts = ydl_opts(skip_download=True, extract_flat=False)
        if target:
            opts["impersonate"] = _imp_value(target)
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(source_url, download=False)
            selectors = build_quality_map(info.get("formats") or [])
            if selectors:
                key, auto = _english_subtitle_info(info)
                return {
                    "title": info.get("title") or source_url,
                    "heights": sorted(selectors),
                    "selectors": selectors,
                    "sizes": estimate_sizes(info.get("formats") or [], info.get("duration")),
                    "subtitle_key": key,
                    "subtitle_auto": auto,
                    "sub_urls": _english_subtitle_urls(info, key, auto),
                    "imp": target,
                    "source_name": source_name,
                    "source_url": source_url,
                }
            last_error = ValueError(f"{source_name} returned no downloadable video formats.")
        except Exception as exc:
            last_error = exc
            log.warning("%s attempt (%s) failed: %s: %s", source_name, target or "default", type(exc).__name__, exc)
            if source_name.lower() != "dailymotion":
                raise
    raise last_error or ValueError(f"{source_name} returned no downloadable video formats.")


def inspect_episode(page_url: str) -> dict:
    raw, soup = fetch_page(page_url)
    title, season, episode = parse_episode_meta(page_url, soup)
    urls = find_source_urls(raw, soup)
    failures = []
    for source_name in ("Dailymotion", "Rumble", "YouTube"):
        for source_url in urls[source_name]:
            try:
                data = inspect_source(source_url, source_name)
                data.update({"title": title, "season": season, "episode": episode})
                return data
            except Exception as e:
                failures.append(f"{source_name}: {type(e).__name__}: {str(e)[:200]}")
    detail = " | ".join(failures[-4:])
    raise LookupError("No usable Dailymotion/Rumble/YouTube video was found on this page." + (f"\n{detail}" if detail else ""))


# =========================
# SUBTITLE (English -> SRT)
# =========================
_VTT_TIME_RE = re.compile(r"((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})")
_VTT_TAG_RE = re.compile(r"</?c(?:\.[\w.-]+)?>|</?v(?:[ .][^>]*)?>|<\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}>")


def _srt_time(ts: str) -> str:
    ts = ts.replace(",", ".")
    main, _, ms = ts.partition(".")
    parts = main.split(":")
    if len(parts) == 2:
        parts.insert(0, "0")
    h, m, s = (int(p) for p in parts)
    return f"{h:02d}:{m:02d}:{s:02d},{ms.ljust(3, '0')[:3]}"


def vtt_to_srt(src: Path, dst: Path) -> None:
    text = src.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    out: list = []
    index = 1
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.rstrip() for ln in block.split("\n") if ln.strip()]
        timing_i = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if timing_i is None:
            continue
        m = _VTT_TIME_RE.search(lines[timing_i])
        if not m:
            continue
        cue = [_VTT_TAG_RE.sub("", ln) for ln in lines[timing_i + 1:]]
        cue = [ln for ln in cue if ln.strip()]
        if not cue:
            continue
        out.extend([str(index), f"{_srt_time(m.group(1))} --> {_srt_time(m.group(2))}", *cue, ""])
        index += 1
    dst.write_text("\n".join(out), encoding="utf-8")


def convert_subtitle_to_srt(src: Path, dst: Path) -> None:
    suffix = src.suffix.lower()
    if suffix == ".srt":
        shutil.copy2(src, dst)
    elif suffix == ".vtt":
        vtt_to_srt(src, dst)
    else:
        if not have_ffmpeg():
            raise RuntimeError("ffmpeg not found (needed to convert this subtitle format).")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), str(dst)], check=True)


def download_subtitle(job: dict, jobdir: Path, base_name: str) -> Optional[Path]:
    """English subtitle: direct URL captured during inspect -> yt-dlp fallback."""
    if not job.get("subtitle_key"):
        return None
    out = jobdir / f"[English] {base_name}.srt"

    for entry in job.get("sub_urls") or []:
        try:
            r = requests.get(entry["url"], headers=HTTP_HEADERS, timeout=30)
            r.raise_for_status()
            if not r.content:
                continue
            text = r.content.decode("utf-8-sig", errors="replace")
            ext = (entry.get("ext") or Path(urlparse(entry["url"]).path).suffix.lstrip(".") or "").lower()
            if "WEBVTT" in text[:200].upper():
                ext = "vtt"
            elif ext not in ("srt", "vtt", "ass", "ssa"):
                ext = "srt"
            raw = jobdir / f".sub_raw.{ext}"
            raw.write_bytes(r.content)
            try:
                convert_subtitle_to_srt(raw, out)
            finally:
                raw.unlink(missing_ok=True)
            if out.exists() and out.stat().st_size > 10:
                return out
        except Exception as e:
            log.warning("direct subtitle failed: %s: %s", type(e).__name__, e)

    if YoutubeDL is None:
        return None
    prefix = jobdir / ".sub_dl"
    for target in _imp_targets(job["source_url"], job.get("imp")):
        opts = ydl_opts(
            skip_download=True,
            outtmpl=tmpl_escape(prefix) + ".%(ext)s",
            subtitleslangs=[job["subtitle_key"]],
            subtitlesformat="srt/vtt/best",
            overwrites=True,
            ignore_no_formats_error=True,
        )
        opts["writeautomaticsub" if job.get("subtitle_auto") else "writesubtitles"] = True
        if target:
            opts["impersonate"] = _imp_value(target)
        try:
            with YoutubeDL(opts) as ydl:
                ydl.extract_info(job["source_url"], download=True)
            for p in sorted(jobdir.glob(".sub_dl*")):
                if p.suffix.lower() in (".srt", ".vtt", ".ass", ".ssa"):
                    convert_subtitle_to_srt(p, out)
                    for q in jobdir.glob(".sub_dl*"):
                        q.unlink(missing_ok=True)
                    if out.exists() and out.stat().st_size > 10:
                        return out
        except Exception as e:
            log.warning("yt-dlp subtitle (%s) failed: %s: %s", target or "default", type(e).__name__, str(e)[:150])
    return None


# =========================
# DOWNLOAD / SOFT-MUX / PROBE
# =========================
def _make_progress_hook(state: dict, job: dict):
    def hook(d: dict) -> None:
        if job.get("cancel"):
            raise Cancelled("cancelled by admin")
        if d.get("status") == "downloading":
            state["downloaded"] = d.get("downloaded_bytes") or 0
            state["total"] = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            state["speed"] = d.get("speed") or 0
            state["eta"] = d.get("eta")
            fi, fc = d.get("fragment_index"), d.get("fragment_count")
            state["frac"] = (fi / fc) if (fi and fc) else None
    return hook


def final_video_path(base: Path) -> Optional[Path]:
    for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4v"):
        p = Path(str(base) + ext)
        if p.exists():
            return p
    return None


def download_quality(job: dict, jobdir: Path, height: int, base_name: str, state: dict) -> Path:
    base = jobdir / f"[{height}p] {base_name}"
    opts = ydl_opts(
        format=job["selectors"][height],
        outtmpl=tmpl_escape(base) + ".%(ext)s",
        merge_output_format="mp4",
        overwrites=True,
        retries=5,
        fragment_retries=10,
        concurrent_fragment_downloads=DOWNLOAD_CONNECTIONS,
        progress_hooks=[_make_progress_hook(state, job)],
    )
    if USE_ARIA2 in ("auto", "on") and have_aria2():
        n = str(ARIA2_CONNECTIONS)
        opts["external_downloader"] = "aria2c"
        opts["external_downloader_args"] = ["-x", n, "-s", n, "-k", "1M", "--file-allocation=none",
                                            "--console-log-level=error", "--summary-interval=0"]

    log.info("Downloading %sp with selector: %s", height, job["selectors"][height])
    last_err = None
    for target in _imp_targets(job["source_url"], job.get("imp")):
        o = dict(opts)
        if target:
            o["impersonate"] = _imp_value(target)
        refreshed = False
        while True:
            try:
                with YoutubeDL(o) as ydl:
                    ydl.extract_info(job["source_url"], download=True)
                last_err = None
                break
            except Cancelled:
                raise
            except Exception as e:
                if job.get("cancel"):
                    raise Cancelled("cancelled by admin")
                last_err = e
                log.warning("%sp via %s failed: %s: %s", height, target or "default", type(e).__name__, str(e)[:200])
                if not refreshed and "requested format is not available" in str(e).lower():
                    # format ids can change between two reads of the same video -> read them again, once
                    refreshed = True
                    try:
                        fresh = inspect_source(job["source_url"], job["source_name"])
                    except Exception as e2:
                        log.warning("re-inspect failed: %s: %s", type(e2).__name__, str(e2)[:150])
                        break
                    job["selectors"] = fresh["selectors"]
                    if height not in fresh["selectors"]:
                        avail = ", ".join(f"{x}p" for x in fresh["heights"]) or "none"
                        raise FormatUnavailable(f"{height}p is not offered by the source right now (available: {avail})")
                    o = dict(o)
                    o["format"] = fresh["selectors"][height]
                    log.info("Retrying %sp with refreshed selector: %s", height, o["format"])
                    continue
                break
        if last_err is None:
            break
    if last_err is not None:
        raise last_err

    path = final_video_path(base)
    if not path:
        raise RuntimeError(f"{height}p download finished but the output file was not found.")
    if path.suffix.lower() != ".mp4":
        # stream copy only: a re-encode would take hours on a small server
        mp4 = Path(str(base) + ".mp4")
        try:
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path), "-c", "copy", str(mp4)],
                           check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"{height}p could not be copied into MP4 ({path.suffix}): {(e.stderr or '')[-200:]}")
        path.unlink(missing_ok=True)
        path = mp4
    return path


def _probe_title(path: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags=title",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        return out or None
    except Exception:
        return None


def embed_soft_sub_and_metadata(video: Path, srt: Optional[Path] = None) -> None:
    """Brand ALL metadata as XiaoBhaiDonghua except the title (kept as original).
    If an SRT is given it is soft-muxed too. Always stream copy - no re-encode."""
    if not have_ffmpeg():
        raise RuntimeError("ffmpeg not found")
    orig_title = _probe_title(video)
    tmp = video.with_name(video.stem + ".__soft.mp4")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video)]
    if srt:
        cmd += ["-i", str(srt)]
    cmd += ["-map", "0:v:0", "-map", "0:a?"]
    if srt:
        cmd += ["-map", "1:0"]
    cmd += ["-c", "copy"]
    if srt:
        cmd += ["-c:s", "mov_text"]
    cmd += ["-map_metadata", "-1", "-map_chapters", "-1"]
    if orig_title:
        cmd += ["-metadata", f"title={orig_title}"]
    for k in ("artist", "author", "album_artist", "composer", "publisher", "copyright", "comment", "description", "encoder"):
        cmd += ["-metadata", f"{k}={BRAND}"]
    cmd += ["-metadata:s:v:0", f"handler_name={BRAND}",
            "-metadata:s:a:0", f"handler_name={BRAND}"]
    if srt:
        cmd += ["-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "title=English",
                "-metadata:s:s:0", f"handler_name={BRAND}",
                "-disposition:s:0", "default"]
    cmd += ["-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
            "-movflags", "+faststart", str(tmp)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        tmp.replace(video)
    except subprocess.CalledProcessError as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg soft-sub/metadata failed: {(e.stderr or '')[-300:]}")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def probe_video(path: Path) -> dict:
    """duration / width / height for Telegram (so the player shows the right size)."""
    info = {"duration": 0, "width": 0, "height": 0}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        ).stdout
        data = json.loads(out or "{}")
        st = (data.get("streams") or [{}])[0]
        info["width"] = int(st.get("width") or 0)
        info["height"] = int(st.get("height") or 0)
        info["duration"] = int(float((data.get("format") or {}).get("duration") or 0))
    except Exception:
        pass
    return info


def make_thumb(path: Path, duration: int) -> Optional[Path]:
    thumb = path.with_suffix(".thumb.jpg")
    try:
        at = max(1, min(30, duration // 10 if duration else 3))
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", str(at), "-i", str(path),
                        "-vframes", "1", "-vf", "scale=320:-2", str(thumb)],
                       check=True, capture_output=True, timeout=120)
        return thumb if thumb.exists() else None
    except Exception:
        return None


# =========================
# TELEGRAM HELPERS
# =========================
try:
    import inspect
    _FORCE_DOC = "force_document" in inspect.signature(Client.send_document).parameters
except Exception:
    _FORCE_DOC = False

LINE = "━━━━━━━━━━━━━━━━━━"


def fmt_size(n) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return "0 B"


def fmt_speed(bps) -> str:
    return f"{fmt_size(bps)}/s" if bps else "--"


def fmt_eta(sec) -> str:
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return "--:--"
    if sec < 0 or sec > 86400:
        return "--:--"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def safe_edit(msg, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    except MessageNotModified:
        pass
    except FloodWait as e:
        await asyncio.sleep(min(getattr(e, "value", None) or getattr(e, "x", 3), 10))
    except Exception as e:
        log.debug("edit failed: %s", e)


def job_header(job: dict) -> str:
    ep = f"S{job['season']} · E{job['episode']}" if job.get("season") is not None else f"E{job['episode']}"
    return f"🎬 <b>{html.escape(job['title'])}</b>\n📺 {ep}  ·  🌐 {html.escape(job.get('source_name', ''))}"


def select_text(job: dict) -> str:
    sel = sorted(job["selected"])
    total = sum((job.get("sizes") or {}).get(h) or 0 for h in sel)
    est = f"  ·  ~{fmt_size(total)}" if total else ""
    sub = "✅ Found" if job.get("subtitle_key") else "❌ Not available"
    return (
        f"{job_header(job)}\n{LINE}\n"
        f"📝 English subtitle: {sub}\n"
        f"🎚 Selected: <b>{len(sel)}</b> of {len(job['heights'])}{est}\n\n"
        "Pick the qualities you want, then press <b>Start</b>.\n"
        "<i>Files are sent in ascending order as documents, with the English subtitle soft-muxed.</i>"
    )


def select_keyboard(job: dict) -> InlineKeyboardMarkup:
    token = job["token"]
    sizes = job.get("sizes") or {}
    rows, row = [], []
    for h in job["heights"]:
        mark = "☑" if h in job["selected"] else "☐"
        size = sizes.get(h)
        label = f"{mark} {h}p" + (f" · {fmt_size(size)}" if size else "")
        row.append(InlineKeyboardButton(label, callback_data=f"pr|{token}|t|{h}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("☑ Select all", callback_data=f"pr|{token}|all"),
                 InlineKeyboardButton("☐ Clear", callback_data=f"pr|{token}|none")])
    n = len(job["selected"])
    rows.append([InlineKeyboardButton(f"▶ Start ({n})" if n else "▶ Start", callback_data=f"pr|{token}|go"),
                 InlineKeyboardButton("✖ Cancel", callback_data=f"pr|{token}|x")])
    return InlineKeyboardMarkup(rows)


def cancel_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✖ Cancel job", callback_data=f"pr|{token}|x")]])


def _stats_line(done_bytes, total_bytes, speed, eta) -> str:
    parts = []
    if total_bytes:
        parts.append(f"{fmt_size(done_bytes)} / {fmt_size(total_bytes)}")
    elif done_bytes:
        parts.append(fmt_size(done_bytes))
    parts.append(f"⚡ {fmt_speed(speed)}")
    parts.append(f"⏱ {fmt_eta(eta)}")
    return "      <code>" + "  ·  ".join(parts) + "</code>"


def render_progress(job: dict) -> str:
    states = job["states"]
    total = len(states)
    done = sum(1 for s in states.values() if s["phase"] == "done")
    finished = sum(1 for s in states.values() if s["phase"] in ("done", "failed"))
    lines = [
        job_header(job),
        LINE,
        f"📝 Subtitle    {job.get('sub_status') or '⏳ Fetching…'}",
        f"📊 Progress    <b>{done}/{total}</b> sent   {bar(finished / total if total else 0)}",
        LINE,
    ]
    for h in sorted(states):
        st = states[h]
        phase = st["phase"]
        if phase == "downloading":
            frac = st.get("frac")
            if frac is None and st.get("total"):
                frac = st["downloaded"] / st["total"]
            frac = frac or 0
            lines.append(f"⬇ <b>{h}p</b>  Downloading")
            lines.append(f"      {bar(frac, 10)} {int(frac * 100)}%")
            lines.append(_stats_line(st.get("downloaded"), st.get("total"), st.get("speed"), st.get("eta")))
        elif phase == "processing":
            lines.append(f"🧩 <b>{h}p</b>  Adding subtitle &amp; metadata")
        elif phase == "ready":
            lines.append(f"📦 <b>{h}p</b>  Ready · waiting to upload")
        elif phase == "uploading":
            tot = st.get("up_total") or 0
            frac = (st.get("up_cur", 0) / tot) if tot else 0
            lines.append(f"⬆ <b>{h}p</b>  Uploading")
            lines.append(f"      {bar(frac, 10)} {int(frac * 100)}%")
            lines.append(_stats_line(st.get("up_cur"), tot, st.get("up_speed"), st.get("up_eta")))
        elif phase == "done":
            avg = f" · avg {fmt_speed(st['avg'])}" if st.get("avg") else ""
            lines.append(f"✅ <b>{h}p</b>  Sent · {fmt_size(st.get('size'))}{avg}")
        elif phase == "failed":
            lines.append(f"❌ <b>{h}p</b>  {html.escape(str(st.get('error', 'Failed'))[:140])}")
        else:
            lines.append(f"⏳ <b>{h}p</b>  Waiting")
    return "\n".join(lines)


async def make_links(log_msg) -> tuple:
    name = get_name(log_msg)
    safe = quote(name, safe="")
    file_hash = get_hash(log_msg)
    stream = f"{URL}watch/{log_msg.id}/{safe}?hash={file_hash}"
    download = f"{URL}{log_msg.id}/{safe}?hash={file_hash}"
    if SHORTLINK:
        stream = await get_shortlink(stream)
        download = await get_shortlink(download)
    return stream, download


# =========================
# THE JOB
# =========================
async def run_job(client: Client, job: dict) -> None:
    token = job["token"]
    status = job["status_msg"]
    chat_id = job["chat_id"]
    jobdir = DOWNLOAD_DIR / token
    jobdir.mkdir(parents=True, exist_ok=True)
    selected = sorted(job["selected"])  # ascending: 360 -> 480 -> 720 -> 1080
    base_name = make_base_name(job["title"], job.get("season"), int(job["episode"]))
    job["states"] = {h: {"phase": "queued", "downloaded": 0, "total": 0, "speed": 0.0, "eta": None, "frac": None}
                     for h in selected}
    job["sub_status"] = "⏳ Fetching…"
    results: dict = {}

    if RUN_LOCK.locked():
        await safe_edit(status, job_header(job) + f"\n{LINE}\n⏳ <b>Queued</b> — waiting for the current episode to finish…",
                        cancel_keyboard(token))

    async with RUN_LOCK:
        stop = asyncio.Event()

        async def ticker() -> None:
            last = None
            while not stop.is_set():
                text = render_progress(job)
                if text != last:
                    await safe_edit(status, text, cancel_keyboard(token))
                    last = text
                try:
                    await asyncio.wait_for(stop.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    pass

        tick = asyncio.create_task(ticker())
        try:
            # ---- 1) English subtitle first ----
            srt: Optional[Path] = None
            try:
                srt = await asyncio.to_thread(download_subtitle, job, jobdir, base_name)
            except Exception as e:
                log.warning("subtitle step failed: %s: %s", type(e).__name__, e)
            if srt:
                job["sub_status"] = "✅ English"
                try:
                    await client.send_document(chat_id, str(srt),
                                               caption=f"📝 <code>{html.escape(srt.name)}</code>\n🌐 English subtitle",
                                               parse_mode=enums.ParseMode.HTML)
                except Exception as e:
                    log.warning("subtitle send failed: %s", e)
            else:
                job["sub_status"] = ("❌ Could not download (videos will have no subtitle)"
                                     if job.get("subtitle_key") else "❌ No English subtitle at the source")
            if job.get("cancel"):
                raise Cancelled()

            # ---- 2) ONE quality at a time, ASCENDING: download -> soft-sub + metadata -> upload -> delete ----
            def cleanup_workdir() -> None:
                """Wipe everything in the job folder (except the subtitle while it is still needed)."""
                for item in list(jobdir.iterdir()):
                    if srt and item == srt:
                        continue
                    try:
                        shutil.rmtree(item) if item.is_dir() else item.unlink(missing_ok=True)
                    except Exception:
                        pass

            async def process_quality(h: int) -> None:
                st = job["states"][h]
                path: Optional[Path] = None
                thumb: Optional[Path] = None
                try:
                    # disk preflight: the file + a temporary copy while muxing
                    est = (job.get("sizes") or {}).get(h) or 0
                    free = shutil.disk_usage(jobdir).free
                    if est and free < est * 2.2:
                        raise RuntimeError(f"Not enough free disk space (need ~{fmt_size(est * 2.2)}, free {fmt_size(free)})")

                    # download (with retries)
                    st["phase"] = "downloading"
                    last_exc = None
                    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
                        try:
                            path = await asyncio.to_thread(download_quality, job, jobdir, h, base_name, st)
                            last_exc = None
                            break
                        except (Cancelled, FormatUnavailable):
                            raise
                        except Exception as e:
                            last_exc = e
                            log.warning("download %sp attempt %s/%s: %s: %s", h, attempt, DOWNLOAD_ATTEMPTS,
                                        type(e).__name__, str(e)[:150])
                            await asyncio.sleep(3 * attempt)
                    if last_exc is not None or path is None:
                        raise last_exc or RuntimeError("Download failed")

                    # metadata branding ALWAYS runs; the subtitle is muxed in too when we have one
                    st["phase"] = "processing"
                    sub_for_mux = srt if (srt and srt.exists()) else None
                    try:
                        await asyncio.to_thread(embed_soft_sub_and_metadata, path, sub_for_mux)
                    except Exception as e:
                        log.warning("mux failed for %sp: %s", h, e)
                        if sub_for_mux:  # retry: at least the metadata branding, without the subtitle
                            try:
                                await asyncio.to_thread(embed_soft_sub_and_metadata, path, None)
                            except Exception as e2:
                                log.warning("metadata-only retry failed for %sp: %s", h, e2)

                    size = path.stat().st_size
                    if size > MAX_UPLOAD_BYTES:
                        raise RuntimeError(f"File is {fmt_size(size)} - above Telegram's ~2 GB limit")

                    # upload as a FILE (document) with live speed + ETA
                    st["phase"] = "uploading"
                    st.update(up_cur=0, up_total=size, up_speed=0.0, up_eta=None, _t=None, _c=0)
                    t_start = time.monotonic()

                    async def progress(current, total, _st=st):
                        if job.get("cancel"):
                            client.stop_transmission()
                        now = time.monotonic()
                        _st["up_cur"], _st["up_total"] = current, total
                        if _st["_t"] is None:
                            _st["_t"], _st["_c"] = now, current
                            return
                        dt = now - _st["_t"]
                        if dt >= 1.0:  # smoothed speed + ETA
                            inst = (current - _st["_c"]) / dt
                            _st["up_speed"] = inst if not _st["up_speed"] else 0.6 * _st["up_speed"] + 0.4 * inst
                            _st["_t"], _st["_c"] = now, current
                            if _st["up_speed"] > 0:
                                _st["up_eta"] = max(0.0, (total - current) / _st["up_speed"])

                    meta = await asyncio.to_thread(probe_video, path)
                    thumb = await asyncio.to_thread(make_thumb, path, meta["duration"])
                    caption = f"🎬 <code>{html.escape(path.name)}</code>\n📐 <b>{h}p</b>  ·  {fmt_size(size)}"
                    kwargs = {"force_document": True} if _FORCE_DOC else {}
                    log_msg = await client.send_document(
                        LOG_CHANNEL, str(path), caption=caption, parse_mode=enums.ParseMode.HTML,
                        thumb=str(thumb) if thumb else None, file_name=path.name,
                        progress=progress, **kwargs,
                    )
                    if log_msg is None:  # Pyrogram returns None when the upload was stopped
                        raise Cancelled()
                    elapsed = max(0.1, time.monotonic() - t_start)
                    st["size"], st["avg"] = size, size / elapsed

                    stream, download = await make_links(log_msg)
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🖥 Stream", url=stream),
                                                InlineKeyboardButton("📥 Download", url=download)]])
                    try:
                        await log_msg.copy(chat_id, reply_markup=kb)
                    except Exception as e:
                        log.warning("copy to chat failed, sending links instead: %s", e)
                        await client.send_message(chat_id, f"🎬 <b>{h}p</b>\n📥 {download}\n🖥 {stream}",
                                                  parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
                    results[h] = (stream, download)
                    st["phase"] = "done"
                except Cancelled:
                    st["phase"] = "failed"
                    st["error"] = "Cancelled"
                except Exception as e:
                    log.exception("quality %sp failed", h)
                    st["phase"] = "failed"
                    st["error"] = str(e)[:140] if isinstance(e, RuntimeError) else f"{type(e).__name__}: {str(e)[:100]}"
                finally:
                    # free the disk BEFORE the next quality starts
                    for p_ in (path, thumb):
                        if p_:
                            Path(p_).unlink(missing_ok=True)
                    cleanup_workdir()

            for h in selected:
                if job.get("cancel"):
                    job["states"][h].update(phase="failed", error="Cancelled")
                    continue
                await process_quality(h)

            # everything done: delete the subtitle too, leave the disk empty
            if srt:
                srt.unlink(missing_ok=True)
            cleanup_workdir()

            stop.set()
            await tick

            # ---- 3) summary ----
            if job.get("cancel"):
                await safe_edit(status, render_progress(job) + f"\n{LINE}\n🛑 <b>Cancelled</b>")
            else:
                ok = sorted(results)
                if ok:
                    verdict = f"✅ <b>Done</b> — {len(ok)} of {len(selected)} qualities sent"
                else:
                    verdict = "❌ <b>Nothing was sent</b> — check the errors above"
                rows = [[InlineKeyboardButton(f"🖥 {h}p Stream", url=results[h][0]),
                         InlineKeyboardButton(f"📥 {h}p Download", url=results[h][1])] for h in ok]
                await safe_edit(status, render_progress(job) + f"\n{LINE}\n{verdict}",
                                InlineKeyboardMarkup(rows) if rows else None)
                try:
                    await client.send_message(
                        chat_id, f"{'✅' if ok else '❌'} <b>{html.escape(job['title'])}</b> — {len(ok)}/{len(selected)} qualities sent.",
                        parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
        except Cancelled:
            await safe_edit(status, job_header(job) + f"\n{LINE}\n🛑 <b>Cancelled</b>")
        except Exception as e:
            log.exception("job crashed")
            await safe_edit(status, job_header(job) + f"\n{LINE}\n❌ <b>Job failed</b>\n"
                            f"<code>{html.escape(type(e).__name__)}: {html.escape(str(e)[:300])}</code>")
        finally:
            stop.set()
            try:
                await tick
            except Exception:
                pass
            shutil.rmtree(jobdir, ignore_errors=True)
            JOBS.pop(token, None)


# =========================
# HANDLERS
# =========================
LINK_RE = r"(?i)https?://(?:www\.)?xiaobhaidonghua\.com/\S+"


def _extract_site_url(text: str) -> Optional[str]:
    m = re.search(r"https?://(?:www\.)?xiaobhaidonghua\.com/[^\s]+", text or "", re.I)
    return clean_url(m.group(0)) if m else None


def _purge_old_jobs() -> None:
    now = time.time()
    for tok, j in list(JOBS.items()):
        if now - j.get("created", now) > 2 * 3600 and not j.get("running"):
            JOBS.pop(tok, None)


@Client.on_message(filters.private & filters.user(ADMINS) & filters.regex(LINK_RE))
async def process_link(client, message):
    url = _extract_site_url(message.text or message.caption or "")
    if not url or not is_own_site_url(url):
        return
    if YoutubeDL is None:
        return await message.reply_text(
            "❌ <b>yt-dlp is not installed.</b>\nMake sure <code>yt-dlp[default,curl-cffi]</code> is in requirements.txt.",
            parse_mode=enums.ParseMode.HTML)
    if not have_ffmpeg():
        return await message.reply_text(
            "❌ <b>ffmpeg was not found.</b>\nMake sure the Dockerfile installs <code>ffmpeg</code>.",
            parse_mode=enums.ParseMode.HTML)
    if not is_episode_page_url(url):
        return await message.reply_text(
            "ℹ️ That doesn't look like an <b>episode</b> link.\nFor now I only support single episode pages "
            "(for example <code>.../episode-5/</code>).", parse_mode=enums.ParseMode.HTML)

    _purge_old_jobs()
    status = await message.reply_text("🔍 <b>Checking the link…</b>\n<i>Reading the page and the available qualities</i>",
                                      parse_mode=enums.ParseMode.HTML)
    try:
        data = await asyncio.to_thread(inspect_episode, url)
    except Exception as e:
        log.exception("inspect failed")
        return await safe_edit(status, f"❌ <b>Couldn't read this link</b>\n<code>{html.escape(str(e)[:700])}</code>")

    token = uuid.uuid4().hex[:8]
    job = {
        **data,
        "token": token,
        "chat_id": message.chat.id,
        "user_id": message.from_user.id,
        "status_msg": status,
        "selected": set(data["heights"]),
        "created": time.time(),
        "cancel": False,
        "running": False,
    }
    JOBS[token] = job
    await safe_edit(status, select_text(job), select_keyboard(job))


@Client.on_callback_query(filters.regex(r"^pr\|"))
async def process_callback(client, query):
    if query.from_user.id not in ADMINS:
        return await query.answer("This is for admins only.", show_alert=True)
    parts = query.data.split("|")
    if len(parts) < 3:
        return await query.answer()
    token, action = parts[1], parts[2]
    job = JOBS.get(token)
    if not job:
        return await query.answer("This job has expired — please send the link again.", show_alert=True)

    if action in ("t", "all", "none") and not job["running"]:
        if action == "t":
            try:
                h = int(parts[3])
            except (IndexError, ValueError):
                return await query.answer()
            if h in job["selected"]:
                job["selected"].discard(h)
            else:
                job["selected"].add(h)
        elif action == "all":
            job["selected"] = set(job["heights"])
        else:
            job["selected"] = set()
        await query.answer()
        return await safe_edit(query.message, select_text(job), select_keyboard(job))

    if action == "go" and not job["running"]:
        if not job["selected"]:
            return await query.answer("Select at least one quality first.", show_alert=True)
        job["running"] = True
        await query.answer("Starting…")
        job["task"] = asyncio.create_task(run_job(client, job))
        return

    if action == "x":
        job["cancel"] = True
        await query.answer("Cancelling…")
        if not job["running"]:
            JOBS.pop(token, None)
            await safe_edit(query.message, job_header(job) + f"\n{LINE}\n🛑 <b>Cancelled</b>")
        return

    await query.answer()
