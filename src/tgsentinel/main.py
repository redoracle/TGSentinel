import asyncio
import logging
import os

from redis import Redis
from telethon import TelegramClient

from .client import make_client, start_ingestion
from .config import load_config
from .digest import send_digest
from .logging_setup import setup_logging
from .metrics import dump
from .store import init_db
from .worker import process_loop


async def _run():
    setup_logging()
    log = logging.getLogger("tgsentinel")
    cfg = load_config()
    engine = init_db(cfg.db_uri)

    client = make_client(cfg)
    r = Redis(host=cfg.redis["host"], port=cfg.redis["port"], decode_responses=True)

    start_ingestion(cfg, client, r)

    await client.start()  # type: ignore[misc]  # interactive login on first run

    log.info("Sentinel started - monitoring %d channels", len(cfg.channels))
    for ch in cfg.channels:
        log.info("  • %s (id: %d)", ch.name, ch.id)

    # Send a test digest on startup if TEST_DIGEST env var is set
    if os.getenv("TEST_DIGEST", "").lower() in ("1", "true", "yes"):
        log.info("TEST_DIGEST enabled, sending digest on startup...")
        await send_digest(
            engine,
            client,
            since_hours=24,
            top_n=cfg.alerts.digest.top_n,
            mode=cfg.alerts.mode,
            channel=cfg.alerts.target_channel,
            channels_config=cfg.channels,
        )
        log.info("Test digest sent!")

    async def worker():
        await process_loop(cfg, client, engine)

    async def periodic():
        # hourly digest
        while True:
            await asyncio.sleep(3600)
            if cfg.alerts.digest.hourly:
                await send_digest(
                    engine,
                    client,
                    since_hours=1,
                    top_n=cfg.alerts.digest.top_n,
                    mode=cfg.alerts.mode,
                    channel=cfg.alerts.target_channel,
                    channels_config=cfg.channels,
                )  # noqa

    async def daily():
        while True:
            await asyncio.sleep(86400)
            if cfg.alerts.digest.daily:
                await send_digest(
                    engine,
                    client,
                    since_hours=24,
                    top_n=cfg.alerts.digest.top_n,
                    mode=cfg.alerts.mode,
                    channel=cfg.alerts.target_channel,
                    channels_config=cfg.channels,
                )

    async def metrics_logger():
        while True:
            await asyncio.sleep(300)
            log.info("Sentinel heartbeat - monitoring active")
            dump()

    await asyncio.gather(worker(), periodic(), daily(), metrics_logger())


if __name__ == "__main__":
    asyncio.run(_run())
