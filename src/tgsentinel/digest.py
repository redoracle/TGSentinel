import logging, datetime as dt
from sqlalchemy import text
from telethon import TelegramClient

log = logging.getLogger(__name__)

DIGEST_QUERY = """
SELECT chat_id, msg_id, score FROM messages
WHERE alerted=1 AND created_at >= :since
ORDER BY score DESC
LIMIT :limit
"""


async def send_digest(
    engine,
    client: TelegramClient,
    since_hours: int,
    top_n: int,
    mode: str,
    channel: str,
):
    now_utc = dt.datetime.now(dt.UTC)
    since = (now_utc - dt.timedelta(hours=since_hours)).replace(tzinfo=None)
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")
    with engine.begin() as con:
        rows = con.execute(
            text(DIGEST_QUERY), {"since": since_str, "limit": top_n}
        ).fetchall()
    if not rows:
        return
    lines = [f"🗞️ Digest — Top {top_n} highlights (last {since_hours}h):"]
    for r in rows:
        lines.append(f"- chat {r.chat_id} msg {r.msg_id} (score {r.score:.2f})")
    msg_text = "\n".join(lines)
    if mode in ("dm", "both"):
        await client.send_message("me", msg_text)
    if mode in ("channel", "both") and channel:
        await client.send_message(channel, msg_text)
