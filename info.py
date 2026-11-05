import re
from os import environ

# .env file support (local run). On Koyeb just set env variables in dashboard.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

id_pattern = re.compile(r'^-?\d+$')


def _required(name):
    value = environ.get(name, '').strip()
    if not value:
        raise SystemExit(f"❌ Missing required environment variable: {name}")
    return value


def _bool(name, default=False):
    return environ.get(name, str(default)).strip().lower() in ('true', '1', 'yes', 'on')


# Bot information
SESSION = environ.get('SESSION', 'XiaoBhaiStreamBot')
API_ID = int(_required('API_ID'))
API_HASH = _required('API_HASH')
BOT_TOKEN = _required('BOT_TOKEN')

# Web server
PORT = int(environ.get('PORT', '8080'))

# Online Stream and Download
MULTI_CLIENT = False
SLEEP_THRESHOLD = int(environ.get('SLEEP_THRESHOLD', '60'))
PING_INTERVAL = int(environ.get('PING_INTERVAL', '1200'))  # 20 minutes

# Public URL of your app, e.g. https://your-app.koyeb.app/  (trailing slash auto-added)
URL = environ.get('URL', '').strip()
if URL and not URL.endswith('/'):
    URL += '/'
KEEP_ALIVE = bool(URL) and _bool('KEEP_ALIVE', True)

# Admins, Channels & Users
LOG_CHANNEL = int(_required('LOG_CHANNEL'))
ADMINS = [int(a) if id_pattern.search(a) else a for a in environ.get('ADMINS', '7475888984').split()]

# Owner (only this person can use /restart). Defaults to the first ADMINS entry.
OWNER_ID = int(environ.get('OWNER_ID', str(ADMINS[0]) if ADMINS and str(ADMINS[0]).lstrip('-').isdigit() else '0'))

# Koyeb redeploy (optional - without these, /restart just restarts the process)
KOYEB_API_TOKEN = environ.get('KOYEB_API_TOKEN', '').strip()
KOYEB_SERVICE_ID = environ.get('KOYEB_SERVICE_ID', '').strip()

# MongoDB information
DATABASE_URI = _required('DATABASE_URI')
DATABASE_NAME = environ.get('DATABASE_NAME', 'XiaoBhaiCluster')

# Shortlink Info (set the API key in env, never hardcode it)
SHORTLINK = _bool('SHORTLINK', False)
SHORTLINK_URL = environ.get('SHORTLINK_URL', 'api.shareus.io')
SHORTLINK_API = environ.get('SHORTLINK_API', '')

# Branding
WEBSITE_URL = environ.get('WEBSITE_URL', 'https://xiaobhaidonghua.com')
SITE_NAME = environ.get('SITE_NAME', 'Xiao Bhai Donghua')

# Access control
# False (default) = ONLY the ADMINS can use the bot, everybody else is politely refused.
# True            = anybody can make links (admin commands stay admin-only).
PUBLIC_MODE = _bool('PUBLIC_MODE', False)

# Force Subscribe: non-admin users must join ALL these channels before the bot works.
# Space separated. Use channel IDs (-100...) or @usernames. The bot must be ADMIN in each channel.
# Example: FORCE_SUB_CHANNELS=-1001111111111 -1002222222222 @mychannel
FORCE_SUB_CHANNELS = [int(c) if id_pattern.search(c) else c.lstrip('@')
                      for c in environ.get('FORCE_SUB_CHANNELS', '').split()]
# Optional: join links in the SAME order (needed only for private channels if the bot can't create invite links)
FORCE_SUB_LINKS = environ.get('FORCE_SUB_LINKS', '').split()

# Storage / auto cleanup (free servers have a tiny disk)
DOWNLOAD_DIR = environ.get('DOWNLOAD_DIR', '/tmp/xiaobhai_dl')
MIN_FREE_MB = int(environ.get('MIN_FREE_MB', '300'))              # alert + auto-clean below this
CLEAN_INTERVAL_MIN = int(environ.get('CLEAN_INTERVAL_MIN', '10'))  # how often the cleaner runs
STALE_MINUTES = int(environ.get('STALE_MINUTES', '60'))            # leftover files older than this are removed
