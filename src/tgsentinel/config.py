import os, yaml, logging
from dataclasses import dataclass, field
from typing import List, Dict, Any

log = logging.getLogger(__name__)


@dataclass
class ChannelRule:
    id: int
    name: str = ""
    vip_senders: List[int] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    reaction_threshold: int = 0
    reply_threshold: int = 0
    rate_limit_per_hour: int = 10


@dataclass
class DigestCfg:
    hourly: bool = True
    daily: bool = True
    top_n: int = 10


@dataclass
class AlertsCfg:
    mode: str = "dm"  # dm|channel|both
    target_channel: str = ""
    digest: DigestCfg = field(default_factory=DigestCfg)


@dataclass
class AppCfg:
    telegram_session: str
    api_id: int
    api_hash: str
    alerts: AlertsCfg
    channels: List[ChannelRule]
    interests: List[str]
    redis: Dict[str, Any]
    db_uri: str
    embeddings_model: str | None
    similarity_threshold: float


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def load_config(path="config/tgsentinel.yml") -> AppCfg:
    with open(path, "r", encoding="utf-8") as f:
        y = yaml.safe_load(f)

    api_id_str = os.getenv("TG_API_ID")
    if not api_id_str:
        raise ValueError("TG_API_ID environment variable is required")
    api_id = int(api_id_str)

    api_hash = os.getenv("TG_API_HASH")
    if not api_hash:
        raise ValueError("TG_API_HASH environment variable is required")

    redis = {
        "host": os.getenv("REDIS_HOST", "localhost"),
        "port": int(os.getenv("REDIS_PORT", "6379")),
        "stream": os.getenv("REDIS_STREAM", "tgsentinel:messages"),
        "group": os.getenv("REDIS_GROUP", "workers"),
        "consumer": os.getenv("REDIS_CONSUMER", "worker-1"),
    }

    db_uri = os.getenv("DB_URI", "sqlite:////app/data/sentinel.db")
    model = os.getenv("EMBEDDINGS_MODEL", None) or None
    sim_thr = float(os.getenv("SIMILARITY_THRESHOLD", "0.42"))

    digest_defaults = DigestCfg(**y.get("alerts", {}).get("digest", {}))
    digest_cfg = DigestCfg(
        hourly=_env_bool("HOURLY_DIGEST", digest_defaults.hourly),
        daily=_env_bool("DAILY_DIGEST", digest_defaults.daily),
        top_n=_env_int("DIGEST_TOP_N", digest_defaults.top_n),
    )

    alerts = AlertsCfg(
        mode=os.getenv("ALERT_MODE", y.get("alerts", {}).get("mode", "dm")),
        target_channel=os.getenv(
            "ALERT_CHANNEL", y.get("alerts", {}).get("target_channel", "")
        ),
        digest=digest_cfg,
    )

    channels = [ChannelRule(**c) for c in y.get("channels", [])]
    return AppCfg(
        telegram_session=y["telegram"]["session"],
        api_id=api_id,
        api_hash=api_hash,
        alerts=alerts,
        channels=channels,
        interests=y.get("interests", []),
        redis=redis,
        db_uri=db_uri,
        embeddings_model=model,
        similarity_threshold=sim_thr,
    )
