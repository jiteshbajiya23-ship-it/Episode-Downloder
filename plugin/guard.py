"""Admin-only gate. Runs BEFORE every other handler (group=-100).

Anyone who is not in ADMINS gets a polite refusal and nothing else runs.
Set PUBLIC_MODE=true in the environment to open the bot for everybody again.
"""
import html
import time

from pyrogram import Client, filters, enums, StopPropagation

from Script import script
from info import ADMINS, PUBLIC_MODE, LOG_CHANNEL
from utils import temp

_last_reply: dict = {}    # user_id -> time of last refusal (one reply per minute, no flood)
_reported: set = set()    # strangers already reported to the log channel


@Client.on_message(filters.private & filters.incoming & ~filters.user(ADMINS), group=-100)
async def block_strangers(client, message):
    if PUBLIC_MODE:
        return  # open mode: let the other handlers run
    user = message.from_user
    uid = user.id if user else 0
    temp.BLOCKED += 1

    now = time.time()
    if now - _last_reply.get(uid, 0) > 60:
        _last_reply[uid] = now
        try:
            await message.reply_text(script.REFUSE_TXT, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass

    # tell the owner once per stranger
    if uid and uid not in _reported:
        _reported.add(uid)
        try:
            snippet = html.escape((message.text or message.caption or "[media]")[:80])
            await client.send_message(
                LOG_CHANNEL,
                script.BLOCKED_LOG.format(user.mention(style=enums.ParseMode.HTML), uid, snippet),
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
    raise StopPropagation


@Client.on_callback_query(~filters.user(ADMINS), group=-100)
async def block_stranger_buttons(client, query):
    if PUBLIC_MODE:
        return
    try:
        await query.answer("🙅 This bot is private.", show_alert=True)
    except Exception:
        pass
    raise StopPropagation
