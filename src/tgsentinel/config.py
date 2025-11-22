import logging
import os
from dataclasses import dataclass, field
from datetime import time
from enum import Enum
from typing import Any, Dict, List, Optional

import yaml

log = logging.getLogger(__name__)


class DigestSchedule(str, Enum):
    """Supported digest schedule types."""

    HOURLY = "hourly"
    EVERY_4H = "every_4h"
    EVERY_6H = "every_6h"
    EVERY_12H = "every_12h"
    DAILY = "daily"
    WEEKLY = "weekly"
    NONE = "none"  # Instant alerts only, no digest


@dataclass
class ScheduleConfig:
    """Configuration for a specific digest schedule."""

    schedule: DigestSchedule
    enabled: bool = True
    top_n: Optional[int] = None  # Override profile-level top_n
    min_score: Optional[float] = None  # Override profile-level min_score

    # Schedule-specific timing (only applicable to certain schedules)
    daily_hour: int = 8  # For DAILY: hour in UTC (0-23)
    weekly_day: int = 0  # For WEEKLY: day of week (0=Monday, 6=Sunday)
    weekly_hour: int = 8  # For WEEKLY: hour in UTC (0-23)

    def __post_init__(self):
        """Validate schedule configuration."""
        if isinstance(self.schedule, str):
            self.schedule = DigestSchedule(self.schedule)
        if not (0 <= self.daily_hour <= 23):
            raise ValueError(f"daily_hour must be 0-23, got {self.daily_hour}")
        if not (0 <= self.weekly_day <= 6):
            raise ValueError(f"weekly_day must be 0-6, got {self.weekly_day}")
        if not (0 <= self.weekly_hour <= 23):
            raise ValueError(f"weekly_hour must be 0-23, got {self.weekly_hour}")
        if self.min_score is not None and not (0.0 <= self.min_score <= 10.0):
            raise ValueError(f"min_score must be 0.0-10.0, got {self.min_score}")
        if self.top_n is not None:
            if not isinstance(self.top_n, int):
                raise ValueError(
                    f"top_n must be an integer, got {type(self.top_n).__name__}"
                )
            if self.top_n <= 0:
                raise ValueError(f"top_n must be greater than zero, got {self.top_n}")


@dataclass
class ProfileDigestConfig:
    """Digest configuration for a profile (supports up to 3 schedules)."""

    schedules: List[ScheduleConfig] = field(default_factory=list)
    top_n: int = 10  # Default for all schedules
    min_score: float = 5.0  # Default minimum score
    mode: str = "dm"  # dm|channel|both
    target_channel: Optional[str] = None  # Override alert target channel

    def __post_init__(self):
        """Validate digest configuration."""
        if len(self.schedules) > 3:
            raise ValueError(
                f"Maximum 3 schedules per profile, got {len(self.schedules)}"
            )
        if not (0.0 <= self.min_score <= 10.0):
            raise ValueError(f"min_score must be 0.0-10.0, got {self.min_score}")
        if self.mode not in {"dm", "channel", "both"}:
            raise ValueError(f"mode must be dm|channel|both, got {self.mode}")

        # Convert dict schedules to ScheduleConfig objects
        converted = []
        for sched in self.schedules:
            if isinstance(sched, dict):
                converted.append(ScheduleConfig(**sched))
            elif isinstance(sched, ScheduleConfig):
                converted.append(sched)
            else:
                raise ValueError(f"Invalid schedule type: {type(sched)}")
        self.schedules = converted


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

    # Digest configuration (optional, per-profile schedules)
    digest: Optional[ProfileDigestConfig] = None

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
    digest: Optional[ProfileDigestConfig] = None  # Digest schedule override


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

    # Direct digest configuration (takes precedence over profile-level)
    digest: Optional[ProfileDigestConfig] = None


@dataclass
class MonitoredUser:
    id: int
    name: str = ""
    username: str = ""
    enabled: bool = True

    # Two-layer architecture: Profile bindings + overrides
    profiles: List[str] = field(default_factory=list)
    overrides: ChannelOverrides = field(default_factory=ChannelOverrides)

    # Direct digest configuration (takes precedence over profile-level)
    digest: Optional[ProfileDigestConfig] = None


@dataclass
class DigestCfg:
    hourly: bool = True
    daily: bool = False
    top_n: int = 10
    check_interval_seconds: int = 300  # Default 5 minutes


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

    def get_config_dir(self) -> str:
        """Get the configuration directory path.

        Returns environment CONFIG_DIR if set, otherwise defaults to 'config'.

        Returns:
            Absolute or relative path to configuration directory
        """
        return os.getenv("CONFIG_DIR", "config")


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


def _convert_legacy_digest(
    digest_cfg: DigestCfg, mode: str = "dm", target_channel: str = ""
) -> ProfileDigestConfig:
    """Convert legacy DigestCfg (hourly/daily booleans) to new ProfileDigestConfig format.

    This provides backward compatibility for old configurations.

    Args:
        digest_cfg: Legacy DigestCfg with hourly/daily flags
        mode: Alert mode (dm|channel|both)
        target_channel: Target channel for digest delivery

    Returns:
        ProfileDigestConfig with schedules matching the legacy flags
    """
    schedules = []

    if digest_cfg.hourly:
        schedules.append(
            ScheduleConfig(
                schedule=DigestSchedule.HOURLY,
                enabled=True,
                top_n=digest_cfg.top_n,
            )
        )

    if digest_cfg.daily:
        schedules.append(
            ScheduleConfig(
                schedule=DigestSchedule.DAILY,
                enabled=True,
                top_n=digest_cfg.top_n,
                daily_hour=8,  # Default 08:00 UTC
            )
        )

    # If no schedules enabled, return config with empty schedules (instant alerts only)
    return ProfileDigestConfig(
        schedules=schedules,
        top_n=digest_cfg.top_n,
        mode=mode,
        target_channel=target_channel if target_channel else None,
    )


def _load_global_profiles(profiles_path: str) -> Dict[str, ProfileDefinition]:
    """Load global profile definitions from unified YAML files.

    Loads profiles from three separate YAML files in the config directory:
    - profiles_alert.yml (Alert profiles, IDs 1000-1999)
    - profiles_global.yml (Global profiles, IDs 2000-2999)
    - profiles_interest.yml (Interest profiles, IDs 3000-3999)

    Falls back to legacy profiles.yml if the unified files don't exist.

    Args:
        profiles_path: Path to profiles.yml file (or config directory)

    Returns:
        Dictionary mapping profile_id -> ProfileDefinition
    """
    # Determine config directory
    if os.path.isfile(profiles_path):
        config_dir = os.path.dirname(profiles_path)
    else:
        config_dir = (
            profiles_path
            if os.path.isdir(profiles_path)
            else os.path.dirname(profiles_path)
        )

    if not config_dir:
        config_dir = "config"

    # Define the three unified YAML files
    alert_path = os.path.join(config_dir, "profiles_alert.yml")
    global_path = os.path.join(config_dir, "profiles_global.yml")
    interest_path = os.path.join(config_dir, "profiles_interest.yml")
    legacy_path = os.path.join(config_dir, "profiles.yml")

    profiles = {}
    loaded_files = []

    # Helper function to load a single YAML file
    def load_profile_file(
        file_path: str, expected_id_range: Optional[tuple] = None
    ) -> Dict[str, ProfileDefinition]:
        """Load profiles from a single YAML file.

        Args:
            file_path: Path to YAML file
            expected_id_range: Optional tuple (min_id, max_id) for validation

        Returns:
            Dictionary of loaded profiles
        """
        if not os.path.exists(file_path):
            return {}

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            file_profiles = {}
            # Handle both flat dict and nested "profiles" key
            profiles_data = data.get("profiles", data) if "profiles" in data else data

            for profile_id, profile_data in profiles_data.items():
                if not profile_data:  # Skip None/empty entries
                    continue

                try:
                    # Convert profile_id to string if it's an integer
                    profile_id = str(profile_id)

                    # Validate ID range if specified
                    if expected_id_range:
                        try:
                            numeric_id = int(profile_id)
                            min_id, max_id = expected_id_range
                            if not (min_id <= numeric_id <= max_id):
                                log.warning(
                                    f"Profile '{profile_id}' in {os.path.basename(file_path)} outside expected range {expected_id_range}"
                                )
                        except ValueError:
                            pass  # Non-numeric IDs are allowed for interest profiles

                    # Create a copy to avoid modifying the original
                    data_copy = dict(profile_data)

                    # Define fields that are valid for ProfileDefinition
                    valid_fields = {
                        "id",
                        "name",
                        "keywords",
                        "action_keywords",
                        "decision_keywords",
                        "urgency_keywords",
                        "importance_keywords",
                        "release_keywords",
                        "security_keywords",
                        "risk_keywords",
                        "opportunity_keywords",
                        "detect_codes",
                        "detect_documents",
                        "prioritize_pinned",
                        "prioritize_admin",
                        "detect_polls",
                        "scoring_weights",
                        "digest",
                    }

                    # Remove fields not part of ProfileDefinition
                    # This filters out UI-specific fields like 'channels', 'overrides', timestamps, etc.
                    filtered_data = {
                        k: v for k, v in data_copy.items() if k in valid_fields
                    }

                    # Convert digest config if present
                    if "digest" in filtered_data and isinstance(
                        filtered_data["digest"], dict
                    ):
                        filtered_data["digest"] = ProfileDigestConfig(
                            **filtered_data["digest"]
                        )

                    # Ensure id field matches key
                    filtered_data["id"] = profile_id

                    profile = ProfileDefinition(**filtered_data)
                    file_profiles[profile_id] = profile
                except Exception as e:
                    log.error(
                        f"Failed to load profile '{profile_id}' from {file_path}: {e}"
                    )
                    continue

            if file_profiles:
                loaded_files.append(os.path.basename(file_path))
            return file_profiles

        except Exception as e:
            log.error(f"Failed to load profiles from {file_path}: {e}")
            return {}

    # Try loading unified YAML files first
    if (
        os.path.exists(alert_path)
        or os.path.exists(global_path)
        or os.path.exists(interest_path)
    ):
        # Load alert profiles (IDs 1000-1999)
        alert_profiles = load_profile_file(alert_path, expected_id_range=(1000, 1999))
        profiles.update(alert_profiles)

        # Load global profiles (IDs 2000-2999)
        global_profiles_data = load_profile_file(
            global_path, expected_id_range=(2000, 2999)
        )
        profiles.update(global_profiles_data)

        # Load interest profiles (IDs 3000-3999 or named)
        interest_profiles = load_profile_file(
            interest_path, expected_id_range=(3000, 3999)
        )
        profiles.update(interest_profiles)

        if loaded_files:
            log.info(
                f"Loaded {len(profiles)} global profiles from unified YAML files: {', '.join(loaded_files)}"
            )
        else:
            log.info(f"No profiles found in unified YAML files at {config_dir}")

        return profiles

    # Fall back to legacy profiles.yml
    elif os.path.exists(legacy_path):
        legacy_profiles = load_profile_file(legacy_path)
        if legacy_profiles:
            log.info(
                f"Loaded {len(legacy_profiles)} global profiles from legacy profiles.yml (consider migrating to unified YAML files)"
            )
        return legacy_profiles

    else:
        log.info(f"No profile files found at {config_dir}, using empty profiles")
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

    # Convert digest configs to new format if present
    for channel in channels:
        if channel.digest and isinstance(channel.digest, dict):
            channel.digest = ProfileDigestConfig(**channel.digest)

    for user in monitored_users:
        if user.digest and isinstance(user.digest, dict):
            user.digest = ProfileDigestConfig(**user.digest)

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
