import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

import yaml

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

    # Category 1: Direct Action Keywords
    action_keywords: List[str] = field(default_factory=list)

    # Category 2: Decision & Governance Keywords
    decision_keywords: List[str] = field(default_factory=list)

    # Category 4: Urgency & Importance Indicators
    urgency_keywords: List[str] = field(default_factory=list)
    importance_keywords: List[str] = field(default_factory=list)

    # Category 5: Interest-based Keywords (releases, security, etc.)
    release_keywords: List[str] = field(default_factory=list)
    security_keywords: List[str] = field(default_factory=list)

    # Category 6: Structured Data Detection
    detect_codes: bool = True
    detect_documents: bool = True

    # Category 8: Risk Keywords
    risk_keywords: List[str] = field(default_factory=list)

    # Category 9: Opportunity Keywords
    opportunity_keywords: List[str] = field(default_factory=list)

    # Category 10: Metadata-based Detection
    prioritize_pinned: bool = True
    prioritize_admin: bool = True
    detect_polls: bool = True

    # Chat type detection (private vs group/channel)
    is_private: bool = False


@dataclass
class MonitoredUser:
    id: int
    name: str = ""
    username: str = ""


@dataclass
class DigestCfg:
    hourly: bool = True
    daily: bool = False
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
    monitored_users: List[MonitoredUser]
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


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a float") from exc


def load_config(path="config/tgsentinel.yml") -> AppCfg:
    # Ensure config directory exists
    config_dir = os.path.dirname(path)
    if config_dir and not os.path.exists(config_dir):
        os.makedirs(config_dir, exist_ok=True)

    # Create default config if it doesn't exist
    if not os.path.exists(path):
        default_config = """# TG Sentinel Configuration
# This file is auto-generated on first startup

# Channels to monitor (can be updated via UI)
channels: []

# Telegram session file path (relative to app root)
telegram:
  session: "data/tgsentinel.session"

# Alert settings
alerts:
  enabled: true
  min_score: 0.7

# Digest settings
digest:
  hourly: true
  daily: true
  
# Logging
logging:
  level: "INFO"
"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(default_config)

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
    sim_thr = _env_float("SIMILARITY_THRESHOLD", 0.42)

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
    monitored_users = [MonitoredUser(**u) for u in y.get("monitored_users", [])]
    return AppCfg(
        telegram_session=y["telegram"]["session"],
        api_id=api_id,
        api_hash=api_hash,
        alerts=alerts,
        channels=channels,
        monitored_users=monitored_users,
        interests=y.get("interests", []),
        redis=redis,
        db_uri=db_uri,
        embeddings_model=model,
        similarity_threshold=sim_thr,
    )
