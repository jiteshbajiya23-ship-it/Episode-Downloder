import html
import logging
from urllib.parse import quote

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from Script import script, LINE
from info import URL, LOG_CHANNEL, SHORTLINK, WEBSITE_URL, SITE_NAME, ADMINS, PUBLIC_MODE
from database.users_chats_db import db
from utils import temp, get_shortlink, retry_async
from xiaobhai.util.alerts import send_alert
from xiaobhai.util.file_properties import get_name, get_hash, get_media_file_size
from xiaobhai.util.human_readable import humanbytes
from xiaobhai.util.maintenance import revoke_link

HTML = enums.ParseMode.HTML


@Client.on_message(filters.command("start") & filters.incoming & filters.private)
async def start(client, message):
    user = message.from_user
    if not user:
        return
    is_admin = user.id in ADMINS

    # DB problems must never stop the bot from replying
    try:
        if not await db.is_user_exist(user.id):
            await db.add_user(user.id, user.first_name)
            await client.send_message(
                LOG_CHANNEL,
                script.LOG_TEXT_P.format(user.id, user.mention(style=HTML)),
                parse_mode=HTML,
            )
    except Exception as e:
        logging.exception("DB/log error in /start (user not saved)")
        await send_alert("MongoDB / log channel error in /start", exc=e, key="start-db")

    rows = [[InlineKeyboardButton(f"🌐 {SITE_NAME}", url=WEBSITE_URL)]]
    if is_admin:
        rows.insert(0, [InlineKeyboardButton("📊 Status", callback_data="ad|status"),
                        InlineKeyboardButton("🩺 Health", callback_data="ad|health"),
                        InlineKeyboardButton("📖 Help", callback_data="ad|help")])
    text = script.ADMIN_START_TXT if is_admin else script.START_TXT
    await message.reply_text(
        text=text.format(user.mention(style=HTML), temp.U_NAME, html.escape(temp.B_NAME or "")),
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=HTML,
        disable_web_page_preview=True,
    )


async def create_links(client, message):
    """Copy the file into LOG_CHANNEL and reply with stream + download links."""
    media = getattr(message, message.media.value)
    user = message.from_user

    # retried 3x (FloodWait is waited out) before we give up
    log_msg = await retry_async(client.send_cached_media, LOG_CHANNEL, media.file_id)

    name = get_name(log_msg)
    safe_name = quote(name, safe="")
    file_hash = get_hash(log_msg)

    stream = f"{URL}watch/{log_msg.id}/{safe_name}?hash={file_hash}"
    download = f"{URL}{log_msg.id}/{safe_name}?hash={file_hash}"
    if SHORTLINK:
        stream = await get_shortlink(stream)
        download = await get_shortlink(download)

    await log_msg.reply_text(
        text=(
            f"🔗 <b>Link generated</b>\n{LINE}\n"
            f"👤 {user.mention(style=HTML)}  ·  <code>{user.id}</code>\n"
            f"📂 <code>{html.escape(name)}</code>"
        ),
        quote=True,
        parse_mode=HTML,
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📥 Download", url=download),
            InlineKeyboardButton("🖥 Watch", url=stream),
        ]]),
    )

    size = humanbytes(get_media_file_size(message)) or "—"
    text = (
        f"✅ <b>Your link is ready!</b>\n{LINE}\n"
        f"📂 <b>File</b>   <code>{html.escape(name)}</code>\n"
        f"📦 <b>Size</b>   <code>{size}</code>\n"
        f"{LINE}\n"
        f"📥 <b>Download</b>\n<code>{html.escape(download)}</code>\n\n"
        f"🖥 <b>Watch online</b>\n<code>{html.escape(stream)}</code>\n"
        f"{LINE}\n"
        f"♾ <i>Links stay active until you revoke them.</i>"
    )
    await message.reply_text(
        text=text,
        quote=True,
        parse_mode=HTML,
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🖥 Stream", url=stream), InlineKeyboardButton("📥 Download", url=download)],
            [InlineKeyboardButton("🗑 Revoke link", callback_data=f"rv|{log_msg.id}")],
        ]),
    )
    temp.LINKS += 1
    await db.inc_stat("links")


def _retry_markup(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Retry", callback_data=f"lk|{msg_id}")]])


@Client.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def stream_start(client, message):
    try:
        await create_links(client, message)
    except Exception as e:
        logging.exception("Link generation failed")
        await send_alert("Link generation failed", exc=e, key="linkgen")
        await message.reply_text(
            "❌ <b>Couldn't make the link.</b>\n<i>The error was reported. Press Retry to try again.</i>",
            parse_mode=HTML, reply_markup=_retry_markup(message.id))


@Client.on_callback_query(filters.regex(r"^lk\|"))
async def retry_link(client, query):
    if query.from_user.id not in ADMINS and not PUBLIC_MODE:
        return await query.answer("Not for you.", show_alert=True)
    try:
        msg_id = int(query.data.split("|")[1])
        original = await client.get_messages(query.message.chat.id, msg_id)
        if not original or original.empty or not original.media:
            return await query.answer("Original file is gone — please send it again.", show_alert=True)
    except Exception:
        return await query.answer("Could not load the original file.", show_alert=True)
    await query.answer("Retrying…")
    try:
        await query.message.edit_text("🔄 <i>Retrying…</i>", parse_mode=HTML)
        await create_links(client, original)
        await query.message.delete()
    except Exception as e:
        logging.exception("Retry failed")
        await send_alert("Link retry failed", exc=e, key="linkretry")
        try:
            await query.message.edit_text("❌ <b>Still failing.</b> Try again in a minute.",
                                          parse_mode=HTML, reply_markup=_retry_markup(msg_id))
        except Exception:
            pass


@Client.on_callback_query(filters.regex(r"^rv\|"))
async def revoke_button(client, query):
    if query.from_user.id not in ADMINS:
        return await query.answer("Admins only.", show_alert=True)
    try:
        await revoke_link(client, int(query.data.split("|")[1]))
        await query.answer("Link revoked")
        await query.message.edit_text("🗑 <b>Link revoked</b>\n<i>The stream and download links no longer work.</i>",
                                      parse_mode=HTML)
    except Exception as e:
        await query.answer(f"Could not revoke: {str(e)[:100]}", show_alert=True)
