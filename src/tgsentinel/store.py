from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
import logging

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(
  chat_id INTEGER,
  msg_id INTEGER,
  content_hash TEXT,
  score REAL,
  alerted INTEGER DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(chat_id, msg_id)
);

CREATE TABLE IF NOT EXISTS feedback(
  chat_id INTEGER,
  msg_id INTEGER,
  label INTEGER, -- 1=thumbs up, 0=thumbs down
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(chat_id, msg_id)
);
"""


def init_db(db_uri: str) -> Engine:
    engine = create_engine(db_uri, future=True)
    with engine.begin() as con:
        # Execute each CREATE TABLE statement separately
        con.execute(
            text(
                """
CREATE TABLE IF NOT EXISTS messages(
  chat_id INTEGER,
  msg_id INTEGER,
  content_hash TEXT,
  score REAL,
  alerted INTEGER DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(chat_id, msg_id)
)
        """
            )
        )

        con.execute(
            text(
                """
CREATE TABLE IF NOT EXISTS feedback(
  chat_id INTEGER,
  msg_id INTEGER,
  label INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(chat_id, msg_id)
)
        """
            )
        )
    log.info("DB ready")
    return engine


def upsert_message(engine: Engine, chat_id: int, msg_id: int, h: str, score: float):
    with engine.begin() as con:
        con.execute(
            text(
                """
          INSERT INTO messages(chat_id,msg_id,content_hash,score,alerted)
          VALUES(:c,:m,:h,:s,0)
          ON CONFLICT(chat_id,msg_id) DO UPDATE SET score=excluded.score, content_hash=excluded.content_hash
        """
            ),
            {"c": chat_id, "m": msg_id, "h": h, "s": score},
        )


def mark_alerted(engine: Engine, chat_id: int, msg_id: int):
    with engine.begin() as con:
        con.execute(
            text("UPDATE messages SET alerted=1 WHERE chat_id=:c AND msg_id=:m"),
            {"c": chat_id, "m": msg_id},
        )
