import logging
import os
from dataclasses import dataclass, field
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


class DeliveryMode(str, Enum):
    """Supported delivery modes for alerts and digests.

    - NONE: Save to database only, no notifications sent
    - DM: Send instant alerts to target user/channel
    - DIGEST: Send scheduled digest summaries only
    - BOTH: Send both instant alerts and scheduled digests
    """

    NONE = "none"  # Save only, no delivery
    DM = "dm"  # Instant alerts to target
    DIGEST = "digest"  # Scheduled digest summaries only
    BOTH = "both"  # Both instant alerts and digests


# Valid delivery modes (canonical set - 'channel' has been removed)
VALID_DELIVERY_MODES = {"none", "dm", "digest", "both"}
VALID_DELIVERY_MODE_MESSAGE = "none|dm|digest|both"


def normalize_delivery_mode(mode: str | None) -> str | None:
    """Normalize and validate delivery mode.

    Args:
        mode: Raw delivery mode string

    Returns:
        Normalized mode string or None if input was None

    Raises:
        ValueError: If mode is not recognized
    """
    if mode is None:
        return None

    mode = mode.lower().strip()

    if mode not in VALID_DELIVERY_MODES:
        raise ValueError(
            f"Invalid delivery mode: '{mode}'. Must be one of: {VALID_DELIVERY_MODE_MESSAGE}"
        )

    return mode


@dataclass
class ScheduleConfig:
    """Configuration for a specific delivery schedule."""

    schedule: DigestSchedule
    enabled: bool = True
    top_n: Optional[int] = None  # Override profile-level top_n
    min_score: Optional[float] = None  # Override profile-level min_score
    mode: Optional[str] = None  # Delivery mode (none|dm|digest|both)
    target_channel: Optional[str] = None  # Target channel/user for delivery

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
        # Validate delivery mode
        if self.mode is not None:
            self.mode = normalize_delivery_mode(self.mode)


@dataclass
class ProfileDigestConfig:
    """Digest configuration for a profile (supports up to 3 schedules)."""

    schedules: List[ScheduleConfig] = field(default_factory=list)
    top_n: int = 10  # Default for all schedules
    min_score: float = 5.0  # Default minimum score

    def __post_init__(self):
        """Validate digest configuration."""
        if len(self.schedules) > 3:
            raise ValueError(
                f"Maximum 3 schedules per profile, got {len(self.schedules)}"
            )
        if not (0.0 <= self.min_score <= 10.0):
            raise ValueError(f"min_score must be 0.0-10.0, got {self.min_score}")

        # Convert dict schedules to ScheduleConfig objects
        converted = []
        for sched in self.schedules:
            if isinstance(sched, dict):
                if "schedule" not in sched:
                    raise ValueError(
                        "Schedule configuration must include 'schedule' field"
                    )
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
    description: str = ""
    enabled: bool = True  # Whether this profile is active
    keywords: List[str] = field(default_factory=list)
    action_keywords: List[str] = field(default_factory=list)
    decision_keywords: List[str] = field(default_factory=list)
    urgency_keywords: List[str] = field(default_factory=list)
    importance_keywords: List[str] = field(default_factory=list)
    release_keywords: List[str] = field(default_factory=list)
    security_keywords: List[str] = field(default_factory=list)
    risk_keywords: List[str] = field(default_factory=list)
    opportunity_keywords: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    # Detection flags (disabled by default - profiles must explicitly enable)
    detect_codes: bool = False  # Detect code blocks/OTP codes
    detect_documents: bool = False  # Detect media attachments
    detect_links: bool = False  # Detect URLs
    require_forwarded: bool = False  # Only match forwarded messages
    prioritize_pinned: bool = False  # Boost pinned messages
    prioritize_admin: bool = False  # Boost admin messages
    prioritize_private: bool = False  # Boost private chat messages (+0.5)
    detect_polls: bool = False  # Detect polls
    detect_mentions: bool = False  # Detect @mentions (not used yet)
    detect_questions: bool = False  # Detect questions (not used yet)

    # Scoring weights (category → weight multiplier)
    scoring_weights: Dict[str, float] = field(default_factory=dict)

    # Digest configuration (optional, per-profile schedules)
    digest: Optional[ProfileDigestConfig] = None

    # Channel/user bindings (empty list = applies to all monitored entities)
    channels: List[int] = field(default_factory=list)
    users: List[int] = field(default_factory=list)

    # VIP senders (always important)
    vip_senders: List[int] = field(default_factory=list)

    # Webhook integrations (list of webhook service names from config/webhooks.yml)
    webhooks: List[str] = field(default_factory=list)

    # User filtering (blacklist: never alert from these users)
    excluded_users: List[int] = field(default_factory=list)

    # Semantic scoring fields (for interest profiles)
    positive_samples: List[str] = field(
        default_factory=list
    )  # Example messages that should match
    negative_samples: List[str] = field(
        default_factory=list
    )  # Example messages that should NOT match
    threshold: float = 0.4  # Similarity threshold for semantic profiles (0.0-1.0)
    positive_weight: float = 1.0  # Multiplier for positive similarity (0.1-2.0)
    negative_weight: float = (
        0.15  # Penalty multiplier for negative similarity (0.0-0.5)
    )
    min_score: float = 1.0  # Minimum score threshold for alert profiles (keyword-based)

    # Engagement thresholds (trigger +0.5 each when met)
    reaction_threshold: int = 0  # Minimum reactions to boost score (0 = disabled)
    reply_threshold: int = 0  # Minimum replies to boost score (0 = disabled)

    def __post_init__(self):
        """Set default scoring weights if not provided."""
        # Normalise description and tags to avoid downstream type issues
        if self.description is None:
            self.description = ""

        if isinstance(self.tags, str):
            self.tags = [tag.strip() for tag in self.tags.split(",") if tag.strip()]
        elif not isinstance(self.tags, list):
            self.tags = []
        else:
            self.tags = [str(tag).strip() for tag in self.tags if str(tag).strip()]

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
    excluded_users: List[int] = field(default_factory=list)  # Additional excluded users


@dataclass
class ChannelRule:
    id: int
    name: str = ""
    vip_senders: List[int] = field(default_factory=list)
    excluded_users: List[int] = field(
        default_factory=list
    )  # Blacklist: never alert from these users
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

    # Category 6: Structured Data Detection (disabled by default)
    detect_codes: bool = False
    detect_documents: bool = False
    detect_links: bool = False
    require_forwarded: bool = False

    # Category 8: Risk Keywords
    risk_keywords: List[str] = field(default_factory=list)

    # Category 9: Opportunity Keywords
    opportunity_keywords: List[str] = field(default_factory=list)

    # Category 10: Metadata-based Detection (disabled by default)
    prioritize_pinned: bool = False
    prioritize_admin: bool = False
    detect_polls: bool = False

    # Chat type detection (private vs group/channel)
    is_private: bool = False

    # Two-layer architecture: Profile bindings + overrides
    profiles: List[str] = field(default_factory=list)
    overrides: ChannelOverrides = field(default_factory=ChannelOverrides)

    # Direct digest configuration (takes precedence over profile-level)
    digest: Optional[ProfileDigestConfig] = None

    # Webhook integrations (list of webhook service names from config/webhooks.yml)
    webhooks: List[str] = field(default_factory=list)


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

    # User filtering (blacklist: never alert from these users)
    excluded_users: List[int] = field(default_factory=list)


@dataclass
class DigestCfg:
    hourly: bool = True
    daily: bool = False
    top_n: int = 10
    check_interval_seconds: int = 300  # Default 5 minutes


@dataclass
class AlertsCfg:
    mode: str = "dm"  # dm|digest|both (channel deprecated)
    target_channel: str = ""
    min_score: float = 5.0  # Minimum score threshold for alerts (0.0-10.0)
    digest: DigestCfg = field(default_factory=DigestCfg)
    feedback_learning: bool = True  # Enable learning from user feedback (👍/👎)

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
class FeedbackLearningConfig:
    """Feedback learning configuration (Phase 1: Stability Foundation)."""

    enabled: bool = True
    borderline_fp_threshold: int = 3
    severe_fp_threshold: int = 2
    strong_tp_threshold: int = 2
    feedback_window_days: int = 7
    decay_interval_hours: int = 24
    max_threshold_delta: float = 0.25
    max_negative_weight_delta: float = 0.1

    def __post_init__(self):
        """Validate feedback learning configuration constraints."""
        # Validate enabled is bool
        if not isinstance(self.enabled, bool):
            raise ValueError(
                f"FeedbackLearningConfig.enabled must be a bool, got {type(self.enabled).__name__}"
            )

        # Validate integer thresholds are non-negative
        if not isinstance(self.borderline_fp_threshold, int):
            raise ValueError(
                f"FeedbackLearningConfig.borderline_fp_threshold must be an integer, "
                f"got {type(self.borderline_fp_threshold).__name__}"
            )
        if self.borderline_fp_threshold < 0:
            raise ValueError(
                f"FeedbackLearningConfig.borderline_fp_threshold must be non-negative, "
                f"got {self.borderline_fp_threshold}"
            )

        if not isinstance(self.severe_fp_threshold, int):
            raise ValueError(
                f"FeedbackLearningConfig.severe_fp_threshold must be an integer, "
                f"got {type(self.severe_fp_threshold).__name__}"
            )
        if self.severe_fp_threshold < 0:
            raise ValueError(
                f"FeedbackLearningConfig.severe_fp_threshold must be non-negative, got {self.severe_fp_threshold}"
            )

        if not isinstance(self.strong_tp_threshold, int):
            raise ValueError(
                f"FeedbackLearningConfig.strong_tp_threshold must be an integer, "
                f"got {type(self.strong_tp_threshold).__name__}"
            )
        if self.strong_tp_threshold < 0:
            raise ValueError(
                f"FeedbackLearningConfig.strong_tp_threshold must be non-negative, got {self.strong_tp_threshold}"
            )

        # Validate borderline >= severe (more stringent requirement)
        if self.borderline_fp_threshold < self.severe_fp_threshold:
            raise ValueError(
                f"FeedbackLearningConfig.borderline_fp_threshold "
                f"({self.borderline_fp_threshold}) must be >= severe_fp_threshold "
                f"({self.severe_fp_threshold})"
            )

        # Validate positive integer time windows
        if not isinstance(self.feedback_window_days, int):
            raise ValueError(
                f"FeedbackLearningConfig.feedback_window_days must be an integer, "
                f"got {type(self.feedback_window_days).__name__}"
            )
        if self.feedback_window_days <= 0:
            raise ValueError(
                f"FeedbackLearningConfig.feedback_window_days must be positive, got {self.feedback_window_days}"
            )

        if not isinstance(self.decay_interval_hours, int):
            raise ValueError(
                f"FeedbackLearningConfig.decay_interval_hours must be an integer, "
                f"got {type(self.decay_interval_hours).__name__}"
            )
        if self.decay_interval_hours <= 0:
            raise ValueError(
                f"FeedbackLearningConfig.decay_interval_hours must be positive, got {self.decay_interval_hours}"
            )

        # Validate float drift caps are in [0.0, 1.0]
        if not isinstance(self.max_threshold_delta, (int, float)):
            raise ValueError(
                f"FeedbackLearningConfig.max_threshold_delta must be a float, "
                f"got {type(self.max_threshold_delta).__name__}"
            )
        if not (0.0 <= self.max_threshold_delta <= 1.0):
            raise ValueError(
                f"FeedbackLearningConfig.max_threshold_delta must be between 0.0 and 1.0, "
                f"got {self.max_threshold_delta}"
            )

        if not isinstance(self.max_negative_weight_delta, (int, float)):
            raise ValueError(
                f"FeedbackLearningConfig.max_negative_weight_delta must be a float, "
                f"got {type(self.max_negative_weight_delta).__name__}"
            )
        if not (0.0 <= self.max_negative_weight_delta <= 1.0):
            raise ValueError(
                f"FeedbackLearningConfig.max_negative_weight_delta must be between 0.0 and 1.0, "
                f"got {self.max_negative_weight_delta}"
            )


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
    feedback_learning: FeedbackLearningConfig = field(
        default_factory=FeedbackLearningConfig
    )

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


def _extract_int(container: Any, key: str) -> Optional[int]:
    if isinstance(container, dict):
        raw = container.get(key)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


def _extract_float(container: Any, key: str) -> Optional[float]:
    if isinstance(container, dict):
        raw = container.get(key)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    return None


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_profile_digest_config(raw: Any) -> ProfileDigestConfig:
    """Parse profile digest configuration.

    Args:
        raw: Raw digest config (dict or ProfileDigestConfig)

    Returns:
        ProfileDigestConfig instance

    Raises:
        TypeError: If raw is not a dict or ProfileDigestConfig
    """
    if isinstance(raw, ProfileDigestConfig):
        return raw

    if not isinstance(raw, dict):
        raise TypeError(
            f"Profile digest config must be dict or ProfileDigestConfig, got {type(raw).__name__}"
        )

    payload: Dict[str, Any] = dict(raw)

    # Extract standard fields
    min_score_sentinel = object()
    min_score_raw = payload.pop("min_score", min_score_sentinel)
    min_score_provided = min_score_raw is not min_score_sentinel

    top_n_sentinel = object()
    top_n_raw = payload.pop("top_n", top_n_sentinel)
    top_n_provided = top_n_raw is not top_n_sentinel

    schedules_data = payload.pop("schedules", None)

    # Build ProfileDigestConfig
    kwargs: Dict[str, Any] = {}

    if schedules_data is not None:
        kwargs["schedules"] = schedules_data

    if top_n_provided:
        kwargs["top_n"] = _coerce_int(top_n_raw, 10)
    else:
        kwargs["top_n"] = 10

    if min_score_provided:
        kwargs["min_score"] = _coerce_float(min_score_raw, 5.0)
    else:
        kwargs["min_score"] = 5.0

    # Log any unsupported keys
    if payload:
        log.debug(
            "Ignoring unsupported digest config keys: %s",
            ", ".join(sorted(str(key) for key in payload.keys())),
        )

    return ProfileDigestConfig(**kwargs)


def _load_global_profiles(profiles_path: str) -> Dict[str, ProfileDefinition]:
    """Load global profile definitions from unified YAML files.

    Loads profiles from three separate YAML files in the config directory:
    - profiles_alert.yml (Alert profiles, IDs 1000-1999)
    - profiles_global.yml (Global profiles, IDs 2000-2999)
    - profiles_interest.yml (Interest profiles, IDs 3000-3999)

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
                                    f"Profile '{profile_id}' in {os.path.basename(file_path)} "
                                    f"outside expected range {expected_id_range}"
                                )
                        except ValueError:
                            pass  # Non-numeric IDs are allowed for interest profiles

                    # Create a copy to avoid modifying the original
                    data_copy = dict(profile_data)

                    # Map UI field names to ProfileDefinition field names
                    # UI uses categorized keywords like critical_keywords, financial_keywords
                    # Backend uses semantic categories like urgency_keywords, security_keywords
                    field_mapping = {
                        "critical_keywords": "urgency_keywords",  # Critical -> Urgency
                        "financial_keywords": "opportunity_keywords",  # Financial -> Opportunity
                        "general_keywords": "keywords",  # General -> Base keywords
                        "community_keywords": "keywords",  # Community -> Base keywords
                        "project_keywords": "importance_keywords",  # Project -> Importance
                        "technical_keywords": "keywords",  # Technical -> Base keywords
                    }

                    # Apply field mapping and merge keywords
                    merged_keywords = {}
                    for ui_field, backend_field in field_mapping.items():
                        if ui_field in data_copy and data_copy[ui_field]:
                            if backend_field not in merged_keywords:
                                merged_keywords[backend_field] = []
                            # Add keywords from UI field to the corresponding backend field
                            ui_keywords = data_copy[ui_field]
                            if isinstance(ui_keywords, list):
                                merged_keywords[backend_field].extend(ui_keywords)
                            # Remove the UI field from data_copy
                            data_copy.pop(ui_field, None)

                    # Merge mapped keywords with any existing keywords in data_copy
                    for backend_field, keywords in merged_keywords.items():
                        if backend_field in data_copy:
                            # Append to existing keywords
                            existing = data_copy[backend_field]
                            if isinstance(existing, list):
                                data_copy[backend_field] = list(
                                    set(existing + keywords)
                                )
                        else:
                            data_copy[backend_field] = keywords

                    # Define fields that are valid for ProfileDefinition
                    valid_fields = {
                        "id",
                        "name",
                        "description",
                        "enabled",  # Whether profile is active
                        "keywords",
                        "action_keywords",
                        "decision_keywords",
                        "urgency_keywords",
                        "importance_keywords",
                        "release_keywords",
                        "security_keywords",
                        "risk_keywords",
                        "opportunity_keywords",
                        "tags",
                        "detect_codes",
                        "detect_documents",
                        "detect_links",  # URL detection (Alert profiles)
                        "require_forwarded",  # Forward-only filter (Alert profiles)
                        "detect_mentions",  # @mention detection (Alert profiles, in development)
                        "detect_questions",  # Question pattern detection (Alert profiles)
                        "prioritize_pinned",
                        "prioritize_admin",
                        "prioritize_private",
                        "detect_polls",
                        "reaction_threshold",
                        "reply_threshold",
                        "scoring_weights",
                        "digest",
                        "channels",  # Channel bindings (empty = all channels)
                        "users",  # User bindings (empty = all users)
                        "vip_senders",  # Always-important sender IDs
                        "excluded_users",  # Blacklist: never alert from these users
                        "webhooks",  # Webhook service names for routing
                        # Semantic scoring fields (Interest profiles)
                        "positive_samples",  # Example messages that should match
                        "negative_samples",  # Example messages that should NOT match
                        "threshold",  # Similarity threshold for semantic profiles (0.0-1.0)
                        "positive_weight",  # Multiplier for positive similarity (0.1-2.0)
                        "negative_weight",  # Penalty multiplier for negative similarity (0.0-0.5)
                        "min_score",  # Minimum score threshold for alert profiles
                    }

                    # Remove fields not part of ProfileDefinition
                    # This filters out UI-specific fields like 'channels', 'overrides', timestamps, etc.
                    filtered_data = {
                        k: v for k, v in data_copy.items() if k in valid_fields
                    }

                    # Convert digest config if present
                    if "digest" in filtered_data:
                        digest_value = filtered_data.get("digest")
                        if isinstance(digest_value, (dict, ProfileDigestConfig)):
                            filtered_data["digest"] = _parse_profile_digest_config(
                                digest_value
                            )
                        elif digest_value is None:
                            filtered_data["digest"] = None
                        else:
                            log.warning(
                                "Profile '%s' has unsupported digest value of type %s; ignoring",
                                profile_id,
                                type(digest_value).__name__,
                            )
                            filtered_data.pop("digest", None)

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
        mode=os.getenv("NOTIFICATION_MODE", y.get("alerts", {}).get("mode", "dm")),
        target_channel=os.getenv(
            "NOTIFICATION_CHANNEL", y.get("alerts", {}).get("target_channel", "")
        ),
        min_score=_env_float(
            "ALERT_MIN_SCORE", y.get("alerts", {}).get("min_score", 5.0)
        ),
        digest=digest_cfg,
        feedback_learning=_env_bool(
            "FEEDBACK_LEARNING", y.get("alerts", {}).get("feedback_learning", True)
        ),
    )

    channels = [ChannelRule(**c) for c in y.get("channels", [])]
    monitored_users = [MonitoredUser(**u) for u in y.get("monitored_users", [])]

    # Convert digest configs to new format if present
    for channel in channels:
        if channel.digest and isinstance(channel.digest, dict):
            channel.digest = _parse_profile_digest_config(channel.digest)

    for user in monitored_users:
        if user.digest and isinstance(user.digest, dict):
            user.digest = _parse_profile_digest_config(user.digest)

    # Get telegram session path with safe fallback
    telegram_config = y.get("telegram", {})
    telegram_session = telegram_config.get("session", "data/tgsentinel.session")

    # Load global profiles from unified YAML files (profiles_alert.yml, profiles_global.yml, profiles_interest.yml)
    profiles_path = os.path.join(config_dir if config_dir else "config", "profiles.yml")
    global_profiles = _load_global_profiles(profiles_path)

    # Parse feedback_learning section (Phase 1)
    feedback_config = y.get("feedback_learning", {})
    aggregation = feedback_config.get("aggregation", {})
    drift_caps = feedback_config.get("drift_caps", {})

    feedback_learning = FeedbackLearningConfig(
        enabled=feedback_config.get("enabled", True),
        borderline_fp_threshold=_coerce_int(
            aggregation.get("borderline_fp_threshold"), 3
        ),
        severe_fp_threshold=_coerce_int(aggregation.get("severe_fp_threshold"), 2),
        strong_tp_threshold=_coerce_int(aggregation.get("strong_tp_threshold"), 2),
        feedback_window_days=_coerce_int(aggregation.get("feedback_window_days"), 7),
        decay_interval_hours=_coerce_int(aggregation.get("decay_interval_hours"), 24),
        max_threshold_delta=_coerce_float(drift_caps.get("max_threshold_delta"), 0.25),
        max_negative_weight_delta=_coerce_float(
            drift_caps.get("max_negative_weight_delta"), 0.1
        ),
    )

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
        feedback_learning=feedback_learning,
    )
