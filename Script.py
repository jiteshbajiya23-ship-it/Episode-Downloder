LINE = "━━━━━━━━━━━━━━━━━━"


class script(object):
    # {0}=user mention  {1}=bot username  {2}=bot name
    START_TXT = """<b>👋 Hello {0}!</b>

I turn any <b>video, audio or file</b> into a fast
<u>stream</u> and <u>download</u> link.

━━━━━━━━━━━━━━━━━━
📤 <b>Send me a file</b> to get started
🌐 <a href="https://xiaobhaidonghua.com">xiaobhaidonghua.com</a>
━━━━━━━━━━━━━━━━━━
<i>Powered by <a href="https://t.me/{1}">{2}</a></i>"""

    ADMIN_START_TXT = """<b>👑 Welcome back, {0}!</b>

Your admin panel is ready.

━━━━━━━━━━━━━━━━━━
📤 <b>Send a file</b> → stream + download link
🔗 <b>Send an episode link</b> → download &amp; upload
━━━━━━━━━━━━━━━━━━
📊 /status   🩺 /health   🏓 /ping
🧹 /cleanup  🐞 /errors   🗑 /revoke
📢 /broadcast   📖 /help
━━━━━━━━━━━━━━━━━━
<i>Powered by <a href="https://t.me/{1}">{2}</a></i>"""

    HELP_TXT = """<b>📖 Help — admin commands</b>
━━━━━━━━━━━━━━━━━━
📊 <b>/status</b> — CPU, RAM, disk, jobs, users
🩺 <b>/health</b> — live check of Telegram, MongoDB, web, channel
🏓 <b>/ping</b> — bot response speed
🧹 <b>/cleanup</b> — delete leftover download files now
🐞 <b>/errors</b> — last errors (<code>/errors clear</code> to reset)
🗑 <b>/revoke</b> <code>&lt;link or id&gt;</code> — kill a stream/download link
📢 <b>/broadcast</b> — reply to a message to send it to all users
━━━━━━━━━━━━━━━━━━
📤 Send any <b>file</b> → get links
🔗 Send an <b>xiaobhaidonghua.com episode link</b> → pick qualities and upload
♻️ Failed qualities get a <b>Retry</b> button, errors go to the log channel."""

    REFUSE_TXT = """🙅 <b>I talk to my owner only.</b>

<i>This is a private bot — only my owner can use me.</i>"""

    BLOCKED_LOG = """🚫 <b>Stranger blocked</b>
━━━━━━━━━━━━━━━━━━
👤 {0}
🆔 <code>{1}</code>
💬 <i>{2}</i>"""

    LOG_TEXT_P = """🆕 <b>New user</b>
━━━━━━━━━━━━━━━━━━
🆔 <code>{}</code>
👤 {}"""

    RESTART_TXT = """🔄 <b>Bot restarted</b>
━━━━━━━━━━━━━━━━━━
📅 <code>{}</code>
⏰ <code>{}</code>
🌐 <code>Asia/Kolkata</code>
🧹 Cleanup: <code>{}</code>"""
