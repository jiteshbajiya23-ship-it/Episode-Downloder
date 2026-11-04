import mimetypes
from typing import Any, Optional

from pyrogram import Client
from pyrogram.types import Message
from pyrogram.file_id import FileId

from xiaobhai.server.exceptions import FIleNotFound

MEDIA_TYPES = (
    "audio",
    "document",
    "photo",
    "sticker",
    "animation",
    "video",
    "voice",
    "video_note",
)


def get_media_from_message(message: "Message") -> Any:
    for attr in MEDIA_TYPES:
        media = getattr(message, attr, None)
        if media:
            return media


def _safe_name(media, message_id) -> str:
    """Telegram sometimes sends media without file_name - never return None."""
    name = getattr(media, "file_name", None)
    if name:
        return name
    mime = getattr(media, "mime_type", None) or ""
    ext = mimetypes.guess_extension(mime) or (".mp4" if mime.startswith("video") else "")
    return f"xiaobhai_{message_id}{ext}"


def get_hash(media_msg: Message) -> str:
    media = get_media_from_message(media_msg)
    return (getattr(media, "file_unique_id", "") or "")[:6]


def get_name(media_msg: Message) -> str:
    media = get_media_from_message(media_msg)
    return _safe_name(media, media_msg.id)


def get_media_file_size(m) -> int:
    media = get_media_from_message(m)
    return getattr(media, "file_size", 0) or 0


async def get_file_ids(client: Client, chat_id: int, id: int) -> Optional[FileId]:
    message = await client.get_messages(chat_id, id)
    if not message or message.empty:
        raise FIleNotFound
    media = get_media_from_message(message)
    if not media:
        raise FIleNotFound
    file_id = FileId.decode(media.file_id)
    setattr(file_id, "file_size", getattr(media, "file_size", 0) or 0)
    setattr(file_id, "mime_type", getattr(media, "mime_type", "") or "")
    setattr(file_id, "file_name", _safe_name(media, message.id))
    setattr(file_id, "unique_id", media.file_unique_id)
    return file_id
