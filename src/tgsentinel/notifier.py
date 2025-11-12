import logging

from telethon import TelegramClient

log = logging.getLogger(__name__)


async def notify_dm(client: TelegramClient, title: str, text: str):
    await client.send_message("me", f"🔔 {title}\n{text}")


async def notify_channel(client: TelegramClient, channel: str, title: str, text: str):
    await client.send_message(channel, f"🔔 {title}\n{text}")
