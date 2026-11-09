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
| YTDLP_COOKIES_B64 | no | Base64 of a Dailymotion `cookies.txt` - fixes most `HTTP 401` errors |
| YTDLP_PROXY | no | `http://user:pass@host:port` - use a residential proxy if Dailymotion blocks the server IP |
| INSPECT_RETRIES | no | Retries (with back-off) on 401/403/429/timeouts before giving up (default 3) |
| DOWNLOAD_ATTEMPTS, DOWNLOAD_CONNECTIONS, ARIA2_CONNECTIONS, USE_ARIA2 | no | Download tuning (3 / 8 / 16 / auto) |
| VIDSSAVE_API_URL | no | Turns ON the VidsSave fallback (see below). Empty = fallback off |
| VIDSSAVE_METHOD, VIDSSAVE_PARAM, VIDSSAVE_SEND_AS, VIDSSAVE_EXTRA, VIDSSAVE_HEADERS, VIDSSAVE_TIMEOUT | no | How to talk to VidsSave (see below) |

## Episode downloader (admin only)

Send a `xiaobhaidonghua.com` episode link to the bot:

1. The page is read and the Dailymotion / Rumble / YouTube source is found.
2. You tick the qualities and press **Start**.
3. English subtitle is sent first, then each quality (low to high): download, soft-mux subtitle + metadata, upload, delete from disk.
4. Every file gets **Stream** and **Download** buttons. Failed qualities get a **Retry failed** button.

### Fixing `HTTP 401 Unauthorized` from Dailymotion

The bot now tries things in this order:

1. **yt-dlp** with 3 retries + back-off (default, chrome, firefox impersonation).
2. **VidsSave fallback** (only if `VIDSSAVE_API_URL` is set).

If 401 still shows up often:
- Redeploy with a fresh build (Dockerfile reinstalls the newest yt-dlp; bump `YTDLP_REFRESH` to force it).
- Set `YTDLP_COOKIES_B64` with fresh Dailymotion cookies (`base64 -w0 cookies.txt`).
- Set `YTDLP_PROXY` to a residential proxy - datacenter IPs (Koyeb, Heroku, VPS) are the most common cause.

### VidsSave fallback

The VidsSave site has no documented API, so the request format is configurable instead of hard-coded:

1. Open the VidsSave page in Chrome, press **F12 → Network**, paste a Dailymotion link and press its Download/Start button.
2. Find the request that returns the quality list / links and copy its URL into `VIDSSAVE_API_URL`.
3. Match the request: `VIDSSAVE_METHOD` (POST/GET), `VIDSSAVE_PARAM` (name of the field that carries the video link), `VIDSSAVE_SEND_AS` (`form`, `json` or `query`), and any other required fields in `VIDSSAVE_EXTRA`, e.g. `{"lang":"en"}`. If the site needs special headers, put them in `VIDSSAVE_HEADERS`.
4. The answer can be JSON or HTML - any link that has a quality like `720p` next to it is picked up automatically. Audio-only links are ignored.

Limits of the fallback: **no subtitle** (videos are sent without the soft-sub), direct links can expire (the bot asks for a fresh link once), and the site can change or block you at any time - that is why it is only a fallback. If the site uses a captcha/JavaScript token, a plain request will not work. Check the site's terms before relying on it.

## Web player

`/watch/...` pages have a refreshed dark UI: keyboard shortcuts (Space, arrows, F), **Open in VLC** and **Copy link** buttons.

## Admin commands
/status /health /ping /cleanup /errors /revoke /help /broadcast


