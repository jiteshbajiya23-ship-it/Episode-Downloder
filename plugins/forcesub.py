"""Force Subscribe gate. Runs BEFORE the admin gate in guard.py (group=-101).

Flow for a non-admin user (PUBLIC_MODE stays false):
  1. Hasn't joined all FORCE_SUB_CHANNELS -> sees only the "join" buttons.
  2. Joined everything and pressed "I've Joined" -> "I talk to my owner only".
  3. From then on guard.py gives the normal "private bot" reply.
Admins are never checked. If FORCE_SUB_CHANNELS is empty this whole gate is off.
The bot must be ADMIN in every force-sub channel (to read members).
"""
import logging
import time

from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from info import ADMINS, FORCE_SUB_CHANNELS, FORCE_SUB_LINKS
from xiaobhai.util.alerts import send_alert

HTML = enums.ParseMode.HTML
CACHE_SECONDS = 300          # a verified user is not re-checked for 5 minutes
_ok_until: dict = {}         # user_id -> time until which they count as verified
_link_cache: dict = {}       # channel -> (title, join link)

JOIN_TXT = (
    "🔒 <b>Join our channels first!</b>\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "To use this bot you must join the channel(s) below,\n"
    "then press <b>✅ I've Joined</b>."
)


OWNER_ONLY_TXT = (
    "✅ <b>Thanks for joining!</b>\n\n"
    "🙅 <b>I talk to my owner only.</b>\n"
    "<i>This is a private bot, so I can't help you here.</i>"
)


async def _is_member(client, channel, user_id) -> bool:
    """True if member. If the check itself fails (bot not admin etc.) we let the user in
    and alert the owner, so one broken channel never locks everybody out."""
    try:
        m = await client.get_chat_member(channel, user_id)
        if m.status in (enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED):
            return False
        if m.status == enums.ChatMemberStatus.RESTRICTED:
            return bool(getattr(m, "is_member", True))
        return True
    except UserNotParticipant:
        return False
    except Exception as e:
        logging.exception(f"Force-sub check failed for {channel}")
        await send_alert(f"Force-sub: cannot check channel {channel}",
                         detail="Make the bot ADMIN in this channel / check the ID.",
                         exc=e, key=f"fs-{channel}")
        return True


async def _missing(client, user_id) -> list:
    """Channels the user has NOT joined (empty list = all joined)."""
    if time.time() < _ok_until.get(user_id, 0):
        return []
    missing = []
    for ch in FORCE_SUB_CHANNELS:
        if not await _is_member(client, ch, user_id):
            missing.append(ch)
    if not missing:
        _ok_until[user_id] = time.time() + CACHE_SECONDS
        if len(_ok_until) > 5000:
            _ok_until.clear()
    return missing


async def _join_info(client, channel):
    """(title, join_link) for a channel, cached."""
    if channel in _link_cache:
        return _link_cache[channel]
    idx = FORCE_SUB_CHANNELS.index(channel)
    manual = FORCE_SUB_LINKS[idx] if idx < len(FORCE_SUB_LINKS) else None
    title, link = str(channel), manual
    try:
        chat = await client.get_chat(channel)
        title = chat.title or title
        if not link:
            link = f"https://t.me/{chat.username}" if chat.username else await client.export_chat_invite_link(channel)
    except Exception as e:
        logging.exception(f"Could not build join link for {channel}")
        await send_alert(f"Force-sub: no join link for {channel}",
                         detail="Make the bot admin with 'invite users' right, or set FORCE_SUB_LINKS.",
                         exc=e, key=f"fslink-{channel}")
    if link:
        _link_cache[channel] = (title, link)
    return title, link


async def _join_markup(client, missing) -> InlineKeyboardMarkup:
    rows = []
    for n, ch in enumerate(missing, 1):
        title, link = await _join_info(client, ch)
        if link:
            rows.append([InlineKeyboardButton(f"📢 Join {title}", url=link)])
    rows.append([InlineKeyboardButton("✅ I've Joined", callback_data="fs|check")])
    return InlineKeyboardMarkup(rows)


if FORCE_SUB_CHANNELS:

    @Client.on_message(filters.private & filters.incoming & ~filters.user(ADMINS), group=-101)
    async def force_sub_gate(client, message):
        user = message.from_user
        if not user:
            return
        missing = await _missing(client, user.id)
        if not missing:
            return  # all joined -> normal handlers run
        try:
            await message.reply_text(JOIN_TXT, parse_mode=HTML, quote=True,
                                     reply_markup=await _join_markup(client, missing),
                                     disable_web_page_preview=True)
        except Exception:
            logging.exception("Could not send force-sub message")
        raise StopPropagation

    @Client.on_callback_query(filters.regex(r"^fs\|check$"), group=-101)
    async def force_sub_check(client, query):
        missing = await _missing(client, query.from_user.id)
        if missing:
            try:
                await query.answer("❌ You haven't joined all channels yet!", show_alert=True)
                await query.message.edit_reply_markup(await _join_markup(client, missing))
            except Exception:
                pass
        else:
            await query.answer("✅ Verified!")
            try:
                await query.message.edit_text(OWNER_ONLY_TXT, parse_mode=HTML)
            except Exception:
                pass
        raise StopPropagation

    @Client.on_callback_query(~filters.user(ADMINS) & ~filters.regex(r"^fs\|"), group=-101)
    async def force_sub_buttons(client, query):
        missing = await _missing(client, query.from_user.id)
        if missing:
            try:
                await query.answer("🔒 Join the required channels first, then send /start.", show_alert=True)
            except Exception:
                pass
            raise StopPropagation
