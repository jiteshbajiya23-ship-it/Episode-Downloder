"""Error alerts: every important error is stored for /errors and sent to LOG_CHANNEL."""
import html
import logging
import time
import traceback

from pyrogram import enums

from info import LOG_CHANNEL
from utils import temp

_last_sent: dict = {}
THROTTLE_SECONDS = 60  # the same alert is sent only once per minute (no spam)


async def send_alert(title: str, detail: str = "", exc: BaseException = None,
                     reply_markup=None, key: str = None) -> None:
    """Never raises - an alert problem must not break the bot."""
    try:
        temp.ERROR_COUNT += 1
        reason = f"{type(exc).__name__}: {exc}" if exc else detail
        temp.ERRORS.append((time.time(), title, str(reason)[:300]))

        k = key or f"{title}|{str(reason)[:60]}"
        now = time.time()
        if now - _last_sent.get(k, 0) < THROTTLE_SECONDS:
            return
        _last_sent[k] = now
        if len(_last_sent) > 200:
            _last_sent.clear()

        client = temp.BOT
        if client is None:
            return

        tb = ""
        if exc is not None and exc.__traceback__ is not None:
            tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        body = html.escape((tb or str(reason))[:700])
        text = (
            f"🚨 <b>{html.escape(title)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<code>{body}</code>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🕒 <i>{time.strftime('%d %b %Y, %I:%M:%S %p', time.gmtime(now + 19800))} IST</i>"
        )
        await client.send_message(LOG_CHANNEL, text, parse_mode=enums.ParseMode.HTML,
                                  reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception as e:  # pragma: no cover
        logging.warning(f"Could not send alert: {e}")
