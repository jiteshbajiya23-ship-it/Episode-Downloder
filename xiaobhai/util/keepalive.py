import asyncio
import logging
import aiohttp
from info import PING_INTERVAL, URL


async def ping_server():
    """Pings our own public URL so free hosts do not put the app to sleep."""
    while True:
        await asyncio.sleep(PING_INTERVAL)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(URL) as resp:
                    logging.info(f"Pinged server, status: {resp.status}")
        except Exception as e:
            logging.warning(f"Keep-alive ping failed: {e}")
