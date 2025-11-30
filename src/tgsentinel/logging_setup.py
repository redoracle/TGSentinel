import logging
import os


def setup_logging():
    """Configure logging for TG Sentinel application."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Reduce noise from telethon - suppress internal warnings about TL deserialization
    logging.getLogger("telethon").setLevel(logging.ERROR)
    logging.getLogger("telethon.client.updates").setLevel(logging.ERROR)
    logging.getLogger("telethon.network").setLevel(logging.ERROR)
