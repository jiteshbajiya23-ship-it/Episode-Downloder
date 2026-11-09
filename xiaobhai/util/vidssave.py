"""
VidsSave fallback provider.

Used ONLY when yt-dlp fails (for example Dailymotion's "HTTP 401 Unauthorized").
It sends the video link to a downloader website/API and reads the quality -> direct-link list
from the answer.

IMPORTANT: the exact request/response format of the site is NOT hard-coded, so that you can
adjust it from environment variables without touching the code (see README -> "VidsSave fallback").
Fallback stays OFF until VIDSSAVE_API_URL is set.

Env variables
  VIDSSAVE_API_URL   endpoint that returns the links (copy it from the browser Network tab)
  VIDSSAVE_METHOD    POST (default) or GET
  VIDSSAVE_PARAM     name of the field that carries the video link (default: url)
  VIDSSAVE_SEND_AS   form (default) | json | query
  VIDSSAVE_EXTRA     extra fields as JSON, e.g. {"lang":"en"}
  VIDSSAVE_HEADERS   extra headers as JSON, e.g. {"Origin":"https://vidssave.com"}
  VIDSSAVE_TIMEOUT   seconds (default 40)
"""
import json
import logging
import re
from os import environ
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("vidssave")

API_URL = environ.get("VIDSSAVE_API_URL", "").strip()
METHOD = (environ.get("VIDSSAVE_METHOD", "POST").strip() or "POST").upper()
PARAM = environ.get("VIDSSAVE_PARAM", "url").strip() or "url"
SEND_AS = (environ.get("VIDSSAVE_SEND_AS", "form").strip() or "form").lower()
TIMEOUT = int(environ.get("VIDSSAVE_TIMEOUT", "40") or 40)


def _json_env(name: str) -> dict:
    raw = environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        log.warning("%s is not valid JSON - ignored", name)
        return {}


EXTRA = _json_env("VIDSSAVE_EXTRA")
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    **_json_env("VIDSSAVE_HEADERS"),
}

ENABLED = bool(API_URL)

_URL_KEYS = ("url", "link", "download", "downloadUrl", "download_url", "href", "src", "file")
_QUALITY_KEYS = ("height", "quality", "resolution", "res", "label", "format", "name", "text", "title")
_AUDIO_HINT = re.compile(r"\b(mp3|m4a|aac|opus|audio)\b", re.I)
_HEIGHT_RE = re.compile(r"(?<!\d)(144|240|360|426|480|540|576|720|1080|1440|2160|4320)(?!\d)")


class VidsSaveError(RuntimeError):
    pass


def _height_from(text) -> Optional[int]:
    m = _HEIGHT_RE.search(str(text or ""))
    return int(m.group(1)) if m else None


def _size_from(item: dict) -> Optional[int]:
    for key in ("size", "filesize", "file_size", "contentLength", "content_length", "bytes"):
        value = item.get(key)
        try:
            if value not in (None, "") and float(value) > 0:
                return int(float(value))
        except (TypeError, ValueError):
            continue
    return None


def _walk(node, out: list, title: list) -> None:
    if isinstance(node, dict):
        if not title and isinstance(node.get("title"), str) and len(node["title"]) > 2:
            title.append(node["title"].strip())
        url = next((node[k] for k in _URL_KEYS if isinstance(node.get(k), str) and node[k].startswith(("http://", "https://", "//"))), None)
        if url:
            label = " ".join(str(node[k]) for k in _QUALITY_KEYS if node.get(k) not in (None, ""))
            height = _height_from(label)
            if height and not _AUDIO_HINT.search(label + " " + url.split("?")[0][-6:]):
                out.append({"height": height, "url": url, "size": _size_from(node),
                            "mp4": "mp4" in (label + url.split("?")[0]).lower()})
        for value in node.values():
            _walk(value, out, title)
    elif isinstance(node, list):
        for value in node:
            _walk(value, out, title)


def parse_response(text: str, base_url: str = "") -> dict:
    """Return {"title": str|None, "options": {height: {"url","size"}}} from a JSON or HTML answer."""
    found: list = []
    title: list = []
    try:
        _walk(json.loads(text), found, title)
    except ValueError:
        soup = BeautifulSoup(text, "html.parser")
        if soup.title and soup.title.string:
            title.append(soup.title.string.strip())
        for a in soup.find_all("a", href=True):
            label = " ".join([a.get_text(" ", strip=True), a.get("title", ""), a.get("data-quality", "")])
            height = _height_from(label)
            if height and not _AUDIO_HINT.search(label):
                found.append({"height": height, "url": urljoin(base_url, a["href"]), "size": None,
                              "mp4": "mp4" in (label + a["href"]).lower()})
    options: dict = {}
    for item in found:
        if item["url"].startswith("//"):
            item["url"] = "https:" + item["url"]
        cur = options.get(item["height"])
        if cur is None or (item["mp4"] and not cur["mp4"]):  # prefer mp4 over other containers
            options[item["height"]] = item
    return {"title": title[0] if title else None,
            "options": {h: {"url": v["url"], "size": v["size"]} for h, v in sorted(options.items())}}


def fetch(source_url: str) -> dict:
    """Ask VidsSave for the links of `source_url`. Raises VidsSaveError if nothing usable came back."""
    if not ENABLED:
        raise VidsSaveError("VidsSave fallback is not configured (VIDSSAVE_API_URL is empty).")
    payload = {PARAM: source_url, **EXTRA}
    kwargs: dict = {"headers": HEADERS, "timeout": TIMEOUT}
    if METHOD == "GET" or SEND_AS == "query":
        kwargs["params"] = payload
    elif SEND_AS == "json":
        kwargs["json"] = payload
    else:
        kwargs["data"] = payload
    try:
        r = requests.request(METHOD, API_URL, **kwargs)
        r.raise_for_status()
    except requests.RequestException as e:
        raise VidsSaveError(f"VidsSave request failed: {type(e).__name__}: {str(e)[:120]}")
    result = parse_response(r.text, API_URL)
    if not result["options"]:
        raise VidsSaveError("VidsSave answered but no video links were found "
                            "(check VIDSSAVE_API_URL / VIDSSAVE_PARAM / VIDSSAVE_EXTRA).")
    return result
