import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

import yaml

log = logging.getLogger(__name__)


@dataclass
class ProfileDefinition:
    """Global profile definition with keywords and scoring weights."""

    id: str
    name: str = ""
    keywords: List[str] = field(default_factory=list)
    action_keywords: List[str] = field(default_factory=list)
    decision_keywords: List[str] = field(default_factory=list)
    urgency_keywords: List[str] = field(default_factory=list)
    importance_keywords: List[str] = field(default_factory=list)
    release_keywords: List[str] = field(default_factory=list)
    security_keywords: List[str] = field(default_factory=list)
    risk_keywords: List[str] = field(default_factory=list)
    opportunity_keywords: List[str] = field(default_factory=list)

    # Detection flags
    detect_codes: bool = True
    detect_documents: bool = True
    prioritize_pinned: bool = True
    prioritize_admin: bool = True
    detect_polls: bool = True

    # Scoring weights (category → weight multiplier)
    scoring_weights: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        """Set default scoring weights if not provided."""
        if not self.scoring_weights:
            self.scoring_weights = {
                "keywords": 0.8,
                "action": 1.0,
                "decision": 1.1,
                "urgency": 1.5,
                "importance": 0.9,
                "release": 0.8,
                "security": 1.2,
                "risk": 1.0,
                "opportunity": 0.6,
                "vip": 1.0,
                "reactions": 0.5,
                "replies": 0.5,
            }


@dataclass
class ChannelOverrides:
    """Per-channel overrides for bound profiles."""

    keywords_extra: List[str] = field(default_factory=list)
    action_keywords_extra: List[str] = field(default_factory=list)
    urgency_keywords_extra: List[str] = field(default_factory=list)
    scoring_weights: Dict[str, float] = field(default_factory=dict)
    min_score: float | None = None


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

    # Two-layer architecture: Profile bindings + overrides
    profiles: List[str] = field(default_factory=list)
    overrides: ChannelOverrides = field(default_factory=ChannelOverrides)


@dataclass
class MonitoredUser:
    id: int
    name: str = ""
    username: str = ""
    enabled: bool = True

    # Two-layer architecture: Profile bindings + overrides
    profiles: List[str] = field(default_factory=list)
    overrides: ChannelOverrides = field(default_factory=ChannelOverrides)


@dataclass
class DigestCfg:
    hourly: bool = True
    daily: bool = False
    top_n: int = 10


@dataclass
class AlertsCfg:
    mode: str = "dm"  # dm|channel|both
    target_channel: str = ""
    min_score: float = 5.0  # Minimum score threshold for alerts (0.0-10.0)
    digest: DigestCfg = field(default_factory=DigestCfg)

    def __post_init__(self):
        """Validate alert configuration constraints."""
        if not (0.0 <= self.min_score <= 10.0):
            raise ValueError(
                f"AlertsCfg.min_score must be between 0.0 and 10.0, got {self.min_score}"
            )


@dataclass
class RedisCfg:
    host: str = "redis"
    port: int = 6379
    stream: str = "tgsentinel:messages"
    group: str = "workers"
    consumer: str = "worker-1"


@dataclass
class LoggingCfg:
    level: str = "INFO"
    retention_days: int = 30


@dataclass
class DatabaseCfg:
    """Database retention and cleanup configuration."""

    max_messages: int = 200  # Keep only last N messages
    retention_days: int = 30  # Also delete messages older than N days
    cleanup_enabled: bool = True  # Master switch for automatic cleanup
    cleanup_interval_hours: int = 24  # How often to run cleanup (daily)
    vacuum_on_cleanup: bool = True  # Run VACUUM after cleanup to reclaim space
    vacuum_hour: int = 3  # Preferred hour for VACUUM (0-23, default 3 AM)


@dataclass
class SystemCfg:
    redis: RedisCfg = field(default_factory=RedisCfg)
    database_uri: str = "sqlite:////app/data/sentinel.db"
    database: DatabaseCfg = field(default_factory=DatabaseCfg)
    logging: LoggingCfg = field(default_factory=LoggingCfg)
    metrics_endpoint: str = ""  # Optional Prometheus/metrics endpoint URL
    auto_restart: bool = True


@dataclass
class AppCfg:
    telegram_session: str
    api_id: int
    api_hash: str
    alerts: AlertsCfg
    channels: List[ChannelRule]
    monitored_users: List[MonitoredUser]
    interests: List[str]
    system: SystemCfg
    embeddings_model: str | None
    similarity_threshold: float
    global_profiles: Dict[str, ProfileDefinition] = field(default_factory=dict)

    # Legacy compatibility properties
    @property
    def redis(self) -> Dict[str, Any]:
        """Legacy redis dict for backward compatibility."""
        return {
            "host": self.system.redis.host,
            "port": self.system.redis.port,
            "stream": self.system.redis.stream,
            "group": self.system.redis.group,
            "consumer": self.system.redis.consumer,
        }

    @property
    def db_uri(self) -> str:
        """Legacy db_uri for backward compatibility."""
        return self.system.database_uri


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


def _load_global_profiles(profiles_path: str) -> Dict[str, ProfileDefinition]:
    """Load global profile definitions from profiles.yml.

    Args:
        profiles_path: Path to profiles.yml file

    Returns:
        Dictionary mapping profile_id -> ProfileDefinition
    """
    if not os.path.exists(profiles_path):
        log.info(f"No profiles.yml found at {profiles_path}, using empty profiles")
        return {}

    try:
        with open(profiles_path, "r", encoding="utf-8") as f:
            profiles_data = yaml.safe_load(f) or {}

        profiles = {}
        for profile_id, data in profiles_data.get("profiles", {}).items():
            try:
                # Create ChannelOverrides if present in YAML (for validation)
                if "overrides" in data:
                    data.pop("overrides")  # Not part of ProfileDefinition

                profile = ProfileDefinition(id=profile_id, **data)
                profiles[profile_id] = profile
            except Exception as e:
                log.error(f"Failed to load profile '{profile_id}': {e}")
                continue

        log.info(f"Loaded {len(profiles)} global profiles from {profiles_path}")
        return profiles

    except Exception as e:
        log.error(f"Failed to load profiles from {profiles_path}: {e}")
        return {}


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

    # System settings: YAML takes precedence over env vars
    system_config = y.get("system", {})

    # Redis configuration
    redis_config = system_config.get("redis", {})
    redis_cfg = RedisCfg(
        host=redis_config.get("host", os.getenv("REDIS_HOST", "redis")),
        port=redis_config.get("port", _env_int("REDIS_PORT", 6379)),
        stream=redis_config.get(
            "stream", os.getenv("REDIS_STREAM", "tgsentinel:messages")
        ),
        group=redis_config.get("group", os.getenv("REDIS_GROUP", "workers")),
        consumer=redis_config.get("consumer", os.getenv("REDIS_CONSUMER", "worker-1")),
    )

    # Database configuration
    database_uri = system_config.get(
        "database_uri", os.getenv("DB_URI", "sqlite:////app/data/sentinel.db")
    )

    # Logging configuration
    logging_config = system_config.get("logging", {})
    logging_cfg = LoggingCfg(
        level=logging_config.get("level", os.getenv("LOG_LEVEL", "INFO")),
        retention_days=logging_config.get(
            "retention_days", _env_int("RETENTION_DAYS", 30)
        ),
    )

    # Database retention configuration
    database_config = system_config.get("database", {})
    database_cfg = DatabaseCfg(
        max_messages=database_config.get(
            "max_messages", _env_int("DB_MAX_MESSAGES", 200)
        ),
        retention_days=database_config.get(
            "retention_days", _env_int("DB_RETENTION_DAYS", 30)
        ),
        cleanup_enabled=database_config.get(
            "cleanup_enabled", _env_bool("DB_CLEANUP_ENABLED", True)
        ),
        cleanup_interval_hours=database_config.get(
            "cleanup_interval_hours", _env_int("DB_CLEANUP_INTERVAL_HOURS", 24)
        ),
        vacuum_on_cleanup=database_config.get(
            "vacuum_on_cleanup", _env_bool("DB_VACUUM_ON_CLEANUP", True)
        ),
        vacuum_hour=database_config.get("vacuum_hour", _env_int("DB_VACUUM_HOUR", 3)),
    )

    # Auto-restart configuration
    auto_restart = system_config.get("auto_restart", _env_bool("AUTO_RESTART", True))

    # Metrics endpoint configuration
    metrics_endpoint = system_config.get(
        "metrics_endpoint", os.getenv("METRICS_ENDPOINT", "")
    )

    # Create SystemCfg
    system_cfg = SystemCfg(
        redis=redis_cfg,
        database_uri=database_uri,
        database=database_cfg,
        logging=logging_cfg,
        metrics_endpoint=metrics_endpoint,
        auto_restart=auto_restart,
    )

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
        min_score=_env_float(
            "ALERT_MIN_SCORE", y.get("alerts", {}).get("min_score", 5.0)
        ),
        digest=digest_cfg,
    )

    channels = [ChannelRule(**c) for c in y.get("channels", [])]
    monitored_users = [MonitoredUser(**u) for u in y.get("monitored_users", [])]

    # Get telegram session path with safe fallback
    telegram_config = y.get("telegram", {})
    telegram_session = telegram_config.get("session", "data/tgsentinel.session")

    # Load global profiles from profiles.yml (same directory as main config)
    profiles_path = os.path.join(config_dir if config_dir else "config", "profiles.yml")
    global_profiles = _load_global_profiles(profiles_path)

    return AppCfg(
        telegram_session=telegram_session,
        api_id=api_id,
        api_hash=api_hash,
        alerts=alerts,
        channels=channels,
        monitored_users=monitored_users,
        interests=y.get("interests", []),
        system=system_cfg,
        embeddings_model=model,
        similarity_threshold=sim_thr,
        global_profiles=global_profiles,
    )
