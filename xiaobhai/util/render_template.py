import logging
import urllib.parse
from pathlib import Path

import jinja2

from info import LOG_CHANNEL, URL, WEBSITE_URL, SITE_NAME
from xiaobhai.bot import StreamBot
from xiaobhai.server.exceptions import InvalidHash
from xiaobhai.util.file_properties import get_file_ids
from xiaobhai.util.human_readable import humanbytes

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"

# autoescape=True -> a file named "<script>..." can not inject HTML into the page
_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=True,
)


async def render_page(id, secure_hash):
    file_data = await get_file_ids(StreamBot, int(LOG_CHANNEL), int(id))
    if file_data.unique_id[:6] != secure_hash:
        logging.debug(f"Invalid hash for message with ID {id}")
        raise InvalidHash

    src = urllib.parse.urljoin(
        URL,
        f"{id}/{urllib.parse.quote(file_data.file_name, safe='')}?hash={secure_hash}",
    )

    tag = (file_data.mime_type or "").split("/")[0].strip()
    template_name = "req.html" if tag in ("video", "audio") else "dl.html"

    return _env.get_template(template_name).render(
        file_name=file_data.file_name.replace("_", " "),
        file_url=src,
        file_size=humanbytes(file_data.file_size),
        site_name=SITE_NAME,
        website_url=WEBSITE_URL,
    )
