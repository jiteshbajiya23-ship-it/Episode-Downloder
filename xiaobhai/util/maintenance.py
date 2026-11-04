"""Auto cleanup of the download folder + helpers for disk space and link revoking."""
import asyncio
import logging
import shutil
import time
from pathlib import Path

from info import DOWNLOAD_DIR, MIN_FREE_MB, STALE_MINUTES, CLEAN_INTERVAL_MIN, LOG_CHANNEL
from xiaobhai.util.alerts import send_alert

BASE = Path(DOWNLOAD_DIR)


def _ensure() -> None:
    BASE.mkdir(parents=True, exist_ok=True)


def disk_info() -> tuple:
    """(free_mb, total_mb)"""
    _ensure()
    u = shutil.disk_usage(BASE)
    return u.free / 1048576, u.total / 1048576


def free_mb() -> float:
    return disk_info()[0]


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def active_tokens() -> set:
    """Folders of running jobs must never be deleted."""
    try:
        from plugins.process import JOBS
        return {t for t, j in JOBS.items() if j.get("running")}
    except Exception:
        return set()


def cleanup(active=None, force: bool = False) -> tuple:
    """Delete leftovers in DOWNLOAD_DIR. Returns (items_removed, bytes_freed).
    force=False -> only items older than STALE_MINUTES
    force=True  -> everything that does not belong to a running job"""
    _ensure()
    active = active_tokens() if active is None else active
    removed = freed = 0
    now = time.time()
    for child in list(BASE.iterdir()):
        if child.name in active:
            continue
        try:
            age_min = (now - child.stat().st_mtime) / 60
            if not force and age_min < STALE_MINUTES:
                continue
            size = _size(child)
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
            removed += 1
            freed += size
        except Exception as e:
            logging.warning(f"cleanup: could not remove {child}: {e}")
    return removed, freed


async def cleanup_loop() -> None:
    """Background task: clean every CLEAN_INTERVAL_MIN minutes and warn when the disk is nearly full."""
    while True:
        await asyncio.sleep(max(1, CLEAN_INTERVAL_MIN) * 60)
        try:
            removed, freed = await asyncio.to_thread(cleanup)
            if removed:
                logging.info(f"Auto cleanup: removed {removed} item(s), freed {freed / 1048576:.1f} MB")
            free = free_mb()
            if free < MIN_FREE_MB:
                # emergency: remove everything not used by a running job
                await asyncio.to_thread(cleanup, None, True)
                free = free_mb()
                if free < MIN_FREE_MB:
                    await send_alert("Low disk space", f"Only {free:.0f} MB free (limit {MIN_FREE_MB} MB).", key="lowdisk")
        except Exception:
            logging.exception("cleanup loop error")


async def revoke_link(client, msg_id: int) -> bool:
    """Delete the file from LOG_CHANNEL -> the stream/download link stops working."""
    await client.delete_messages(LOG_CHANNEL, int(msg_id))
    try:
        from plugins.route import class_cache
        for streamer in class_cache.values():
            streamer.cached_file_ids.pop(int(msg_id), None)
    except Exception:
        pass
    return True
