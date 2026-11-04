import asyncio
import datetime
import logging
import time

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked, PeerIdInvalid

from database.users_chats_db import db
from info import ADMINS


@Client.on_message(filters.command("broadcast") & filters.user(ADMINS))
async def pm_broadcast(bot, message):
    # Usage: reply to any message with /broadcast
    b_msg = message.reply_to_message
    if not b_msg:
        return await message.reply_text("Jis message ko broadcast karna hai uspe reply karke /broadcast bhejo.")

    users = await db.get_all_users()
    sts = await message.reply_text("Broadcasting your message...")
    start_time = time.time()
    total_users = await db.total_users_count()
    done = blocked = deleted = failed = success = 0

    async for user in users:
        if "id" in user:
            ok, reason = await broadcast_messages(int(user["id"]), b_msg)
            if ok:
                success += 1
            elif reason == "Blocked":
                blocked += 1
            elif reason == "Deleted":
                deleted += 1
            else:
                failed += 1
        else:
            failed += 1
        done += 1
        if not done % 20:
            try:
                await sts.edit(
                    f"Broadcast in progress:\n\nTotal Users {total_users}\nCompleted: {done} / {total_users}\n"
                    f"Success: {success}\nBlocked: {blocked}\nDeleted: {deleted}"
                )
            except Exception:
                pass

    time_taken = datetime.timedelta(seconds=int(time.time() - start_time))
    await sts.edit(
        f"Broadcast Completed in {time_taken}.\n\nTotal Users: {total_users}\nCompleted: {done} / {total_users}\n"
        f"Success: {success}\nBlocked: {blocked}\nDeleted: {deleted}\nFailed: {failed}"
    )


async def broadcast_messages(user_id, message):
    try:
        await message.copy(chat_id=user_id)
        return True, "Success"
    except FloodWait as e:
        await asyncio.sleep(getattr(e, "value", None) or getattr(e, "x", 5))
        return await broadcast_messages(user_id, message)
    except InputUserDeactivated:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id} removed from DB (deleted account).")
        return False, "Deleted"
    except UserIsBlocked:
        logging.info(f"{user_id} blocked the bot.")
        return False, "Blocked"
    except PeerIdInvalid:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id} PeerIdInvalid")
        return False, "Error"
    except Exception:
        return False, "Error"
