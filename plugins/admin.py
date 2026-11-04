"""Admin commands: /status /health /ping /cleanup /errors /revoke /help  (+ refresh buttons)."""
import asyncio
import html
import re
import shutil
import time

import aiohttp
import psutil
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from Script import script, LINE
from database.users_chats_db import db
from info import ADMINS, URL, LOG_CHANNEL, MIN_FREE_MB, DOWNLOAD_DIR
from utils import temp, bar, fmt_uptime
from xiaobhai.util.maintenance import disk_info, cleanup, revoke_link

HTML = enums.ParseMode.HTML
ADMIN = filters.user(ADMINS)


def _kb(*buttons):
    return InlineKeyboardMarkup([list(buttons)])


NAV = InlineKeyboardMarkup([
    [InlineKeyboardButton("📊 Status", callback_data="ad|status"), InlineKeyboardButton("🩺 Health", callback_data="ad|health")],
    [InlineKeyboardButton("🧹 Cleanup", callback_data="ad|clean"), InlineKeyboardButton("🐞 Errors", callback_data="ad|errors")],
])


def _mb(v: float) -> str:
    return f"{v / 1024:.2f} GB" if v >= 1024 else f"{v:.0f} MB"


# ------------------------------------------------------------------ STATUS
async def build_status() -> str:
    try:
        from plugins.process import JOBS, RUN_LOCK
        running = [j for j in JOBS.values() if j.get("running")]
        waiting = max(0, len(running) - (1 if RUN_LOCK.locked() else 0))
        job_line = f"{'🟢 1 running' if RUN_LOCK.locked() else '⚪ idle'}  ·  ⏳ {waiting} queued"
    except Exception:
        job_line = "⚪ unknown"

    cpu = psutil.cpu_percent(interval=0.3)
    ram = psutil.virtual_memory()
    free, total = await asyncio.to_thread(disk_info)
    used_frac = 1 - (free / total) if total else 0

    try:
        users = await db.total_users_count()
    except Exception:
        users = "❌ DB down"
    total_links = await db.get_stat("links")

    return (
        "📊 <b>Bot Status</b>\n"
        f"{LINE}\n"
        f"🟢 <b>Uptime</b>   <code>{fmt_uptime(time.time() - temp.START_TIME)}</code>\n"
        f"⚙️ <b>CPU</b>      <code>{cpu:.0f}%</code>  {bar(cpu / 100)}\n"
        f"🧠 <b>RAM</b>      <code>{ram.percent:.0f}%</code>  {bar(ram.percent / 100)}\n"
        f"💽 <b>Disk</b>     <code>{_mb(free)} free</code>  {bar(used_frac)}\n"
        f"{LINE}\n"
        f"⚡ <b>Jobs</b>     {job_line}\n"
        f"👥 <b>Users</b>    <code>{users}</code>\n"
        f"🔗 <b>Links</b>    <code>{temp.LINKS}</code> now · <code>{total_links}</code> total\n"
        f"🚫 <b>Blocked</b>  <code>{temp.BLOCKED}</code>\n"
        f"🐞 <b>Errors</b>   <code>{temp.ERROR_COUNT}</code>\n"
        f"{LINE}\n"
        f"<i>Updated {time.strftime('%I:%M:%S %p', time.gmtime(time.time() + 19800))} IST</i>"
    )


@Client.on_message(filters.command("status") & filters.private & ADMIN)
async def status_cmd(client, message):
    await message.reply_text(await build_status(), parse_mode=HTML, reply_markup=NAV)


# ------------------------------------------------------------------ HEALTH
async def _timed(coro):
    t = time.perf_counter()
    await coro
    return (time.perf_counter() - t) * 1000


async def build_health() -> str:
    checks = []

    async def add(name, coro_fn):
        try:
            ms = await asyncio.wait_for(_timed(coro_fn()), timeout=10)
            checks.append((name, True, f"{ms:.0f} ms"))
        except Exception as e:
            checks.append((name, False, (str(e) or type(e).__name__)[:60]))

    await add("Telegram API", lambda: client_get_me())
    await add("MongoDB", lambda: db.ping())
    await add("Log channel", lambda: temp.BOT.get_chat(LOG_CHANNEL))

    async def web():
        if not URL:
            raise RuntimeError("URL env variable is empty")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(URL + "health") as r:
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")
    await add("Web server", web)

    free, _ = await asyncio.to_thread(disk_info)
    checks.append(("Disk space", free >= MIN_FREE_MB, f"{_mb(free)} free"))
    for tool in ("ffmpeg", "ffprobe", "aria2c"):
        ok = shutil.which(tool) is not None
        checks.append((tool, ok, "installed" if ok else "missing"))
    try:
        import yt_dlp  # noqa
        checks.append(("yt-dlp", True, "installed"))
    except Exception:
        checks.append(("yt-dlp", False, "missing"))

    bad = sum(1 for _, ok, _ in checks if not ok)
    lines = [f"{'✅' if ok else '❌'} <b>{n}</b> — <code>{html.escape(d)}</code>" for n, ok, d in checks]
    verdict = "🟢 <b>All systems healthy</b>" if not bad else f"🟠 <b>{bad} problem(s) found</b>"
    return f"🩺 <b>Health Check</b>\n{LINE}\n" + "\n".join(lines) + f"\n{LINE}\n{verdict}"


async def client_get_me():
    return await temp.BOT.get_me()


@Client.on_message(filters.command("health") & filters.private & ADMIN)
async def health_cmd(client, message):
    sts = await message.reply_text("🩺 <i>Checking everything…</i>", parse_mode=HTML)
    await sts.edit_text(await build_health(), parse_mode=HTML, reply_markup=NAV)


# ------------------------------------------------------------------ PING / HELP
@Client.on_message(filters.command("ping") & filters.private & ADMIN)
async def ping_cmd(client, message):
    t = time.perf_counter()
    sts = await message.reply_text("🏓 …")
    await sts.edit_text(f"🏓 <b>Pong!</b>  <code>{(time.perf_counter() - t) * 1000:.0f} ms</code>", parse_mode=HTML)


@Client.on_message(filters.command("help") & filters.private & ADMIN)
async def help_cmd(client, message):
    await message.reply_text(script.HELP_TXT, parse_mode=HTML, reply_markup=NAV)


# ------------------------------------------------------------------ CLEANUP
async def run_cleanup_text() -> str:
    removed, freed = await asyncio.to_thread(cleanup, None, True)
    free, _ = await asyncio.to_thread(disk_info)
    return (
        "🧹 <b>Cleanup done</b>\n"
        f"{LINE}\n"
        f"🗑 Removed   <code>{removed}</code> item(s)\n"
        f"💾 Freed      <code>{_mb(freed / 1048576)}</code>\n"
        f"💽 Free now  <code>{_mb(free)}</code>\n"
        f"{LINE}\n<i>Running downloads were not touched.</i>"
    )


@Client.on_message(filters.command("cleanup") & filters.private & ADMIN)
async def cleanup_cmd(client, message):
    await message.reply_text(await run_cleanup_text(), parse_mode=HTML, reply_markup=NAV)


# ------------------------------------------------------------------ ERRORS
def build_errors() -> str:
    if not temp.ERRORS:
        return f"🐞 <b>Recent errors</b>\n{LINE}\n✅ <i>No errors since the last restart.</i>"
    rows = []
    for ts, title, reason in reversed(temp.ERRORS):
        t = time.strftime("%d %b %I:%M %p", time.gmtime(ts + 19800))
        rows.append(f"🕒 <i>{t}</i>\n<b>{html.escape(title)}</b>\n<code>{html.escape(reason[:160])}</code>")
    return f"🐞 <b>Recent errors</b> ({temp.ERROR_COUNT} total)\n{LINE}\n" + f"\n{LINE}\n".join(rows[:8])


@Client.on_message(filters.command("errors") & filters.private & ADMIN)
async def errors_cmd(client, message):
    if len(message.command) > 1 and message.command[1].lower() == "clear":
        temp.ERRORS.clear()
        temp.ERROR_COUNT = 0
        return await message.reply_text("✅ Error list cleared.")
    await message.reply_text(build_errors(), parse_mode=HTML, reply_markup=NAV)


# ------------------------------------------------------------------ REVOKE
@Client.on_message(filters.command("revoke") & filters.private & ADMIN)
async def revoke_cmd(client, message):
    arg = " ".join(message.command[1:]) or (message.reply_to_message.text if message.reply_to_message and message.reply_to_message.text else "")
    m = re.fullmatch(r"\s*(\d+)\s*", arg) or re.search(r"/(?:watch/)?(\d+)/", arg)
    if not m:
        return await message.reply_text(
            "🗑 <b>Revoke a link</b>\nSend <code>/revoke &lt;link or id&gt;</code> or reply to a link with /revoke.",
            parse_mode=HTML)
    try:
        await revoke_link(client, int(m.group(1)))
        await message.reply_text(f"🗑 <b>Link revoked</b> — file <code>#{m.group(1)}</code> removed. The link is dead now.",
                                 parse_mode=HTML)
    except Exception as e:
        await message.reply_text(f"❌ Could not revoke: <code>{html.escape(str(e)[:200])}</code>", parse_mode=HTML)


# ------------------------------------------------------------------ BUTTONS
@Client.on_callback_query(filters.regex(r"^ad\|"))
async def admin_buttons(client, query):
    if query.from_user.id not in ADMINS:  # also protects PUBLIC_MODE
        return await query.answer("Admins only.", show_alert=True)
    action = query.data.split("|")[1]
    await query.answer("Updating…" if action in ("status", "health") else None)
    if action == "status":
        text = await build_status()
    elif action == "health":
        text = await build_health()
    elif action == "clean":
        text = await run_cleanup_text()
    elif action == "errors":
        text = build_errors()
    else:
        text = script.HELP_TXT
    try:
        await query.message.edit_text(text, parse_mode=HTML, reply_markup=NAV)
    except Exception:
        pass  # "message not modified" when nothing changed
