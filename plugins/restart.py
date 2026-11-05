"""/restart - OWNER ONLY. Redeploys the service on Koyeb (or restarts the process as a fallback).

Env variables (set in the Koyeb dashboard):
  OWNER_ID=123456789          (defaults to the first ID in ADMINS)
  KOYEB_API_TOKEN=...         (Koyeb -> Organization settings -> API -> create token)
  KOYEB_SERVICE_ID=...        (Koyeb -> your service -> Settings, or the service URL)

Usage:
  /restart          -> refuses if a download is running
  /restart force    -> restarts anyway (running download is lost)
"""
import asyncio
import logging
import os
import sys

import requests
from pyrogram import Client, filters, enums

from info import OWNER_ID, KOYEB_API_TOKEN, KOYEB_SERVICE_ID

log = logging.getLogger("restart")
HTML = enums.ParseMode.HTML


def _koyeb_redeploy() -> str:
    """Trigger a fresh deployment of the service. Returns '' on success, else an error text."""
    try:
        r = requests.post(
            f"https://app.koyeb.com/v1/services/{KOYEB_SERVICE_ID}/redeploy",
            headers={"Authorization": f"Bearer {KOYEB_API_TOKEN}", "Content-Type": "application/json"},
            json={"deployment_group": "prod", "use_cache": True},
            timeout=30,
        )
        if r.status_code in (200, 201, 202):
            return ""
        return f"Koyeb API {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def _restart_process():
    """Fallback: replace the current process with a fresh one."""
    os.execv(sys.executable, [sys.executable] + sys.argv)


@Client.on_message(filters.command("restart") & filters.private & filters.user(OWNER_ID))
async def restart_cmd(client: Client, message):
    force = len(message.command) > 1 and message.command[1].lower() == "force"

    try:
        from plugins.process import RUN_LOCK
        busy = RUN_LOCK.locked()
    except Exception:
        busy = False
    if busy and not force:
        return await message.reply_text(
            "⚠️ A download is running right now. Restarting will cancel it.\n"
            "Send <code>/restart force</code> to restart anyway.",
            parse_mode=HTML,
        )

    if KOYEB_API_TOKEN and KOYEB_SERVICE_ID:
        note = await message.reply_text("♻️ Asking Koyeb to redeploy… the bot will be back in a minute or two.")
        err = await asyncio.to_thread(_koyeb_redeploy)
        if err:
            log.error("Koyeb redeploy failed: %s", err)
            await note.edit_text(f"❌ Redeploy failed:\n<code>{err}</code>", parse_mode=HTML)
        return

    await message.reply_text("♻️ Restarting the process… (set KOYEB_API_TOKEN + KOYEB_SERVICE_ID for a full redeploy)")
    await asyncio.sleep(1)
    _restart_process()
