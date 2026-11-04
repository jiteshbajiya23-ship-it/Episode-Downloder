# Xiao Bhai File Stream Bot

Telegram bot jo kisi bhi video/file ka direct stream aur download link bana deta hai.
Made for https://xiaobhaidonghua.com

## Environment variables

| Name | Required | Note |
|------|----------|------|
| API_ID, API_HASH | yes | my.telegram.org se |
| BOT_TOKEN | yes | @BotFather se |
| LOG_CHANNEL | yes | Channel ID (-100...), bot ko channel me admin banao |
| DATABASE_URI | yes | MongoDB connection string |
| URL | yes | App ka public link, jaise https://your-app.koyeb.app/ |
| PORT | no | Default 8080 (Koyeb port se match karo) |
| ADMINS | no | Space separated user IDs |
| SHORTLINK, SHORTLINK_URL, SHORTLINK_API | no | Link shortener (default off) |
| WEBSITE_URL, SITE_NAME | no | Branding |
| PUBLIC_MODE | no | `false` (default) = only ADMINS can use the bot, strangers are refused. `true` = anybody can make links |
| DOWNLOAD_DIR | no | Temp folder for downloads (default /tmp/xiaobhai_dl) |
| MIN_FREE_MB | no | Auto-clean + alert when free disk is below this (default 300) |
| CLEAN_INTERVAL_MIN, STALE_MINUTES | no | Auto cleanup every N minutes (10) of files older than N minutes (60) |

## Admin commands
/status /health /ping /cleanup /errors /revoke /help /broadcast

## Credits

Based on VJBots/FileToLink (https://github.com/VJBots/FileToLink), licensed under GPL-3.0. See LICENSE.
