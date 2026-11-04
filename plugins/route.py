import re
import time
import asyncio
import math
import logging
import secrets
import mimetypes
from urllib.parse import quote

from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine

from xiaobhai.bot import multi_clients, work_loads
from xiaobhai.server.exceptions import FIleNotFound, InvalidHash
from xiaobhai.util.custom_dl import ByteStreamer
from xiaobhai.util.render_template import render_page
from xiaobhai.util.alerts import send_alert
from utils import temp

routes = web.RouteTableDef()
class_cache = {}


def _safe(handler):
    """One place for all error handling of the web routes."""
    async def wrapper(request: web.Request):
        try:
            return await handler(request)
        except InvalidHash as e:
            raise web.HTTPForbidden(text=e.message)
        except FIleNotFound as e:
            raise web.HTTPNotFound(text=e.message)
        except web.HTTPException:
            raise
        except (BadStatusLine, ConnectionResetError):
            return web.Response(status=499)
        except Exception as e:
            logging.exception("Web route error")
            asyncio.ensure_future(send_alert("Web route error", exc=e, key="web500"))
            raise web.HTTPInternalServerError(text="Internal server error")
    return wrapper


def _parse(path: str, request: web.Request):
    match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)
    if match:
        return int(match.group(2)), match.group(1)
    found = re.search(r"(\d+)(?:\/\S+)?", path)
    if not found:
        raise FIleNotFound
    return int(found.group(1)), request.rel_url.query.get("hash")


@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return web.json_response({"status": "ok", "service": "Xiao Bhai File Stream"})


@routes.get("/health", allow_head=True)
async def health_route_handler(request):
    return web.json_response({"status": "ok", "uptime_seconds": int(time.time() - temp.START_TIME)})


@routes.get(r"/watch/{path:.+}", allow_head=True)
@_safe
async def watch_handler(request: web.Request):
    id, secure_hash = _parse(request.match_info["path"], request)
    return web.Response(text=await render_page(id, secure_hash), content_type="text/html")


@routes.get(r"/{path:.+}", allow_head=True)
@_safe
async def stream_handler(request: web.Request):
    id, secure_hash = _parse(request.match_info["path"], request)
    return await media_streamer(request, id, secure_hash)


async def media_streamer(request: web.Request, id: int, secure_hash: str):
    range_header = request.headers.get("Range", 0)

    index = min(work_loads, key=work_loads.get)
    faster_client = multi_clients[index]

    if faster_client in class_cache:
        tg_connect = class_cache[faster_client]
    else:
        tg_connect = ByteStreamer(faster_client)
        class_cache[faster_client] = tg_connect

    file_id = await tg_connect.get_file_properties(id)

    if file_id.unique_id[:6] != secure_hash:
        logging.debug(f"Invalid hash for message with ID {id}")
        raise InvalidHash

    file_size = file_id.file_size

    if range_header:
        from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
        from_bytes = int(from_bytes)
        until_bytes = int(until_bytes) if until_bytes else file_size - 1
    else:
        from_bytes = request.http_range.start or 0
        until_bytes = (request.http_range.stop or file_size) - 1

    if (until_bytes > file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
        return web.Response(
            status=416,
            body="416: Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_size = 1024 * 1024
    until_bytes = min(until_bytes, file_size - 1)

    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1

    req_length = until_bytes - from_bytes + 1
    part_count = math.ceil(until_bytes / chunk_size) - math.floor(offset / chunk_size)
    body = tg_connect.yield_file(
        file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
    )

    mime_type = file_id.mime_type
    file_name = file_id.file_name

    if not mime_type:
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    if not file_name:
        file_name = f"{secrets.token_hex(2)}.unknown"

    return web.Response(
        status=206 if range_header else 200,
        body=body,
        headers={
            "Content-Type": f"{mime_type}",
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(req_length),
            # filename* keeps Chinese / non-ASCII donghua names working
            "Content-Disposition": f"attachment; filename*=UTF-8\x27\x27{quote(file_name)}",
            "Accept-Ranges": "bytes",
        },
    )
