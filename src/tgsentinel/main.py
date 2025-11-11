import asyncio, logging, os
from redis import Redis
from telethon import TelegramClient
from .logging_setup import setup_logging
from .config import load_config
from .store import init_db
from .client import make_client, start_ingestion
from .worker import process_loop
from .metrics import dump
from .digest import send_digest


async def _run():
    setup_logging()
    log = logging.getLogger("tgsentinel")
    cfg = load_config()
    engine = init_db(cfg.db_uri)

    client = make_client(cfg)
    r = Redis(host=cfg.redis["host"], port=cfg.redis["port"], decode_responses=True)

    start_ingestion(cfg, client, r)

    await client.start()  # interactive login on first run

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
                )

    async def metrics_logger():
        while True:
            await asyncio.sleep(300)
            dump()

    await asyncio.gather(worker(), periodic(), daily(), metrics_logger())


if __name__ == "__main__":
    asyncio.run(_run())
