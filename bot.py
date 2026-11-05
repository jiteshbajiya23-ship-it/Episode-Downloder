import logging, logging.config, asyncio, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import date, datetime

import pytz

# Get logging configurations
logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("aiohttp.web").setLevel(logging.ERROR)

from aiohttp import web
from pyrogram import idle, enums
from pyrogram.errors import FloodWait

from info import *
from utils import temp
from Script import script
from database.users_chats_db import db
from plugins import web_server
from xiaobhai.bot import StreamBot
from xiaobhai.bot.clients import initialize_clients
from xiaobhai.util.keepalive import ping_server
from xiaobhai.util.alerts import send_alert
from xiaobhai.util.maintenance import cleanup, cleanup_loop

# Handlers in /plugins are loaded by Pyrogram itself (plugins={"root": "plugins"}),
# so we must NOT import them again manually.


def _start_with_floodwait():
    """Telegram can rate-limit bot logins (FloodWait). Instead of crashing (which makes
    the host restart us and re-trigger the limit), wait it out while answering health checks."""
    for attempt in range(5):
        try:
            StreamBot.start()
            return
        except FloodWait as e:
            wait = int(e.value) + 5
            logging.warning(f"FloodWait: Telegram asked to wait {wait}s (attempt {attempt + 1}/5). Sleeping...")

            class _Health(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"waiting for Telegram flood limit")

                def log_message(self, *a):
                    pass

            httpd = None
            try:
                httpd = HTTPServer(("0.0.0.0", PORT), _Health)
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
            except Exception as ex:
                logging.warning(f"Health server not started: {ex}")
            time.sleep(wait)
            if httpd:
                httpd.shutdown()
                httpd.server_close()
    raise SystemExit("Could not log in to Telegram after repeated FloodWait")


_start_with_floodwait()
loop = asyncio.get_event_loop()


async def start():
    print('\nInitializing Xiao Bhai Stream Bot')
    me = await StreamBot.get_me()
    await initialize_clients()

    temp.BOT = StreamBot
    temp.ME = me.id
    temp.U_NAME = me.username
    temp.B_NAME = me.first_name

    # any crash inside a background task is reported to the log channel
    def _on_loop_error(_loop, ctx):
        exc = ctx.get("exception")
        logging.error("Unhandled error: %s", ctx.get("message"), exc_info=exc)
        asyncio.ensure_future(send_alert("Unhandled error", detail=str(ctx.get("message", "")), exc=exc, key="loop"))
    asyncio.get_running_loop().set_exception_handler(_on_loop_error)

    # auto cleanup: wipe leftovers from the previous run, then keep cleaning in the background
    try:
        removed, freed = await asyncio.to_thread(cleanup, set(), True)
        clean_note = f"{removed} item(s), {freed / 1048576:.0f} MB freed"
    except Exception as e:
        clean_note = f"failed ({type(e).__name__})"
    asyncio.create_task(cleanup_loop())

    if KEEP_ALIVE:
        asyncio.create_task(ping_server())

    if not URL:
        logging.warning("URL env variable is empty - generated links will be broken!")

    # MongoDB health check (this is the usual reason why /start stays silent)
    from urllib.parse import urlparse, unquote
    try:
        _u = urlparse(DATABASE_URI)
        _pw = unquote(_u.password or "")
        logging.info(f"DB check -> user={_u.username!r} host={_u.hostname!r} pass_len={len(_pw)} has_angle={'<' in _pw or '>' in _pw} has_space={' ' in DATABASE_URI}")
    except Exception as e:
        logging.info(f"DB URI parse issue: {e}")
    db_error = None
    try:
        await db.ping()
        logging.info("MongoDB connected ✅")
    except Exception as e:
        db_error = e
        logging.error(f"MongoDB connection FAILED ❌ : {e}")

    # Restart message in log channel (bot must be admin there)
    try:
        now = datetime.now(pytz.timezone('Asia/Kolkata'))
        await StreamBot.send_message(
            chat_id=LOG_CHANNEL,
            parse_mode=enums.ParseMode.HTML,
            text=script.RESTART_TXT.format(date.today(), now.strftime("%I:%M:%S %p"), clean_note),
        )
        if db_error:
            await StreamBot.send_message(
                chat_id=LOG_CHANNEL,
                text=f"⚠️ <b>MongoDB is not connecting</b>\n\n<code>{str(db_error)[:900]}</code>\n\n"
                     f"Check DATABASE_URI and Atlas Network Access (0.0.0.0/0).",
                parse_mode=enums.ParseMode.HTML,
            )
    except Exception as e:
        logging.warning(f"Could not send message to LOG_CHANNEL (bot channel me admin hai?): {e}")

    app = web.AppRunner(await web_server())
    await app.setup()
    await web.TCPSite(app, "0.0.0.0", PORT).start()
    logging.info(f"Web server running on port {PORT}")
    await idle()


if __name__ == '__main__':
    try:
        loop.run_until_complete(start())
    except KeyboardInterrupt:
        logging.info('Service Stopped Bye 👋')
