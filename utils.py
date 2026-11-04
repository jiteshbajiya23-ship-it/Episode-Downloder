import asyncio
import collections
import logging
import time

from pyrogram.errors import FloodWait
from shortzy import Shortzy

from info import SHORTLINK_URL, SHORTLINK_API


class temp(object):
    ME = None
    BOT = None
    U_NAME = None
    B_NAME = None
    # runtime stats (shown in /status)
    START_TIME = time.time()
    ERRORS = collections.deque(maxlen=15)   # (timestamp, title, reason)
    ERROR_COUNT = 0
    BLOCKED = 0                              # refused messages from non-admins
    LINKS = 0                                # links made since the last restart


async def get_shortlink(link):
    """Shorten a link. If shortener fails, return the original link so the bot never breaks."""
    try:
        shortzy = Shortzy(api_key=SHORTLINK_API, base_site=SHORTLINK_URL)
        return await shortzy.convert(link)
    except Exception as e:
        logging.warning(f"Shortlink failed, using original link: {e}")
        return link


async def retry_async(func, *args, attempts: int = 3, base_delay: float = 2.0, **kwargs):
    """Run an async function, retrying on errors. FloodWait is waited out properly."""
    last = None
    for i in range(1, attempts + 1):
        try:
            return await func(*args, **kwargs)
        except FloodWait as e:
            last = e
            wait = getattr(e, "value", None) or getattr(e, "x", 5)
            await asyncio.sleep(min(int(wait) + 1, 60))
        except Exception as e:
            last = e
            if i == attempts:
                break
            await asyncio.sleep(base_delay * i)
    raise last


def bar(fraction: float, width: int = 10) -> str:
    fraction = max(0.0, min(1.0, fraction or 0.0))
    filled = int(round(fraction * width))
    return "▰" * filled + "▱" * (width - filled)


def fmt_uptime(seconds: float) -> str:
    seconds = int(seconds)
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"
