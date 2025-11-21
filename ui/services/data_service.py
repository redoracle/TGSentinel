"""Data aggregation service for TG Sentinel UI.

This module provides business logic for computing dashboard summaries,
health metrics, and loading various data feeds from the UI database and Redis.

ARCHITECTURAL NOTE: This service accesses UI DB only. For sentinel data,
use HTTP API endpoints to the sentinel service.
"""

from __future__ import annotations

import logging
import os
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, cast

logger = logging.getLogger(__name__)


class DataService:
    """Service for aggregating and transforming dashboard data."""

    def __init__(
        self,
        redis_client: Any,
        config: Any,
        query_one_func: Any,
        query_all_func: Any,
        get_stream_name_func: Any,
        truncate_func: Any,
        normalize_tags_func: Any,
        format_timestamp_func: Any,
    ):
        """Initialize data service with dependencies.

        Args:
            redis_client: Redis connection
            config: Application configuration
            query_one_func: Function to query single value from UI DB
            query_all_func: Function to query multiple rows from UI DB
            get_stream_name_func: Function to get Redis stream name
            truncate_func: Function to truncate text
            normalize_tags_func: Function to normalize tags
            format_timestamp_func: Function to format timestamps
        """
        self.redis_client = redis_client
        self.config = config
        self._query_one = query_one_func
        self._query_all = query_all_func
        self._get_stream_name = get_stream_name_func
        self._truncate = truncate_func
        self._normalize_tags = normalize_tags_func
        self._format_timestamp = format_timestamp_func

        # Cache for expensive operations
        self._cached_summary: tuple[datetime, Dict[str, Any]] | None = None
        self._cached_health: tuple[datetime, Dict[str, Any]] | None = None

    def compute_summary(self) -> Dict[str, Any]:
        """Compute dashboard summary statistics from Sentinel API.

        Returns:
            Dictionary with messages_ingested, alerts_sent, avg_importance, feedback_accuracy
        """
        now = datetime.now(timezone.utc)
        if self._cached_summary and now - self._cached_summary[0] < timedelta(
            seconds=15
        ):
            return self._cached_summary[1]

        try:
            sentinel_api_url = os.getenv(
                "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
            )
            response = requests.get(
                f"{sentinel_api_url}/stats", params={"hours": 24}, timeout=5
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "ok" and data.get("data"):
                summary = data["data"]
                self._cached_summary = (now, summary)
                return summary

            # Return defaults if no data
            summary = {
                "messages_ingested": 0,
                "alerts_sent": 0,
                "avg_importance": 0.0,
                "feedback_accuracy": 0.0,
            }
            self._cached_summary = (now, summary)
            return summary

        except requests.RequestException as e:
            logger.warning(f"Failed to fetch stats from Sentinel API: {e}")
            # Return cached data if available, otherwise defaults
            if self._cached_summary:
                return self._cached_summary[1]
            return {
                "messages_ingested": 0,
                "alerts_sent": 0,
                "avg_importance": 0.0,
                "feedback_accuracy": 0.0,
            }

    def compute_health(
        self, psutil: Any = None, redis_module: Any = None
    ) -> Dict[str, Any]:
        """Compute system health metrics.

        Args:
            psutil: Optional psutil module for process metrics
            redis_module: Optional redis module for fallback connections

        Returns:
            Dictionary with health status indicators
        """
        now = datetime.now(timezone.utc)
        if self._cached_health and now - self._cached_health[0] < timedelta(seconds=10):
            return self._cached_health[1]

        stream_name = self._get_stream_name()
        redis_depth = 0
        redis_online = False

        if self.redis_client:
            try:
                depth_val = self.redis_client.xlen(stream_name)
                redis_depth = int(depth_val) if depth_val else 0
                redis_online = True
            except Exception as exc:
                logger.debug("Redis depth unavailable: %s", exc)

        # UI DB size
        ui_db_size_mb = 0.0
        try:
            ui_db_path = os.getenv("UI_DB_URI", "sqlite:////app/data/ui.db").replace(
                "sqlite:///", ""
            )
            ui_db_file = Path(ui_db_path)
            if ui_db_file.exists():
                ui_db_size_mb = round(ui_db_file.stat().st_size / (1024 * 1024), 2)
        except Exception:
            pass

        cpu_pct = None
        memory_mb = None
        if psutil:
            try:
                cpu_pct = psutil.cpu_percent(interval=None)
                process = psutil.Process(os.getpid())
                memory_mb = round(process.memory_info().rss / (1024 * 1024), 1)
            except Exception as exc:
                logger.debug("psutil metrics unavailable: %s", exc)

        payload = {
            "redis_stream_depth": redis_depth,
            "database_size_mb": ui_db_size_mb,
            "redis_online": redis_online,
            "cpu_percent": cpu_pct,
            "memory_mb": memory_mb,
        }
        self._cached_health = (now, payload)
        return payload

    def load_live_feed(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Load recent messages from Redis stream.

        Args:
            limit: Maximum number of messages to load

        Returns:
            List of message dictionaries
        """
        import json

        entries: List[Dict[str, Any]] = []
        if not self.redis_client:
            return self._fallback_feed(limit)

        try:
            raw_entries = self.redis_client.xrevrange(
                self._get_stream_name(), count=limit
            )
            iterable: Iterable[Any] = raw_entries or []
            if not isinstance(iterable, list):
                iterable = list(iterable)

            # Build chat_id to channel name mapping
            chat_id_to_name = {}
            if self.config and hasattr(self.config, "channels"):
                for channel in self.config.channels:
                    if hasattr(channel, "id") and hasattr(channel, "name"):
                        chat_id_to_name[channel.id] = channel.name

            for entry_id, payload in iterable:
                data = dict(payload)

                # Parse JSON field if present
                json_str = data.get("json")
                if json_str:
                    try:
                        parsed = json.loads(json_str)
                        data.update(parsed)
                    except Exception:
                        pass

                # Extract chat and sender information
                chat_id = data.get("chat_id")
                chat_name = (
                    data.get("chat_name", "").strip()
                    or data.get("chat_title", "").strip()
                    or data.get("channel", "").strip()
                )

                if not chat_name and chat_id:
                    try:
                        chat_id_int = int(chat_id)
                        chat_name = chat_id_to_name.get(chat_id_int)
                    except (ValueError, TypeError):
                        pass

                if not chat_name:
                    chat_name = "Unknown chat"

                sender_name = data.get("sender_name", "").strip()
                if not sender_name:
                    sender_name = (
                        data.get("sender", "").strip()
                        or data.get("author", "").strip()
                        or "Unknown sender"
                    )

                # Determine avatar URL
                avatar_url = data.get("avatar_url") or data.get("chat_avatar_url")
                sender_id = data.get("sender_id")

                if not avatar_url and sender_id:
                    try:
                        cache_key = f"tgsentinel:user_avatar:{sender_id}"
                        if self.redis_client.exists(cache_key):
                            avatar_url = f"/api/avatar/user/{sender_id}"
                    except Exception:
                        pass

                entries.append(
                    {
                        "id": entry_id,
                        "chat_id": chat_id,
                        "chat_name": chat_name,
                        "sender_id": data.get("sender_id"),
                        "sender": sender_name,
                        "message": self._truncate(
                            data.get("message") or data.get("text")
                        ),
                        "importance": round(float(data.get("importance", 0.0)), 2),
                        "tags": self._normalize_tags(data.get("tags")),
                        "timestamp": self._format_timestamp(
                            data.get("timestamp") or data.get("created_at") or ""
                        ),
                        "avatar_url": avatar_url,
                        "replies": data.get("replies"),
                        "reactions": data.get("reactions"),
                        "is_reply": data.get("is_reply"),
                        "has_media": data.get("has_media"),
                        "media_type": data.get("media_type"),
                    }
                )
        except Exception as exc:
            logger.debug("Failed to read activity feed: %s", exc)

        return entries if entries else self._fallback_feed(limit)

    def _fallback_feed(self, limit: int) -> List[Dict[str, Any]]:
        """Generate fallback feed when Redis is unavailable."""
        now = datetime.now(timezone.utc)
        fallback: List[Dict[str, Any]] = []
        channels_attr = getattr(self.config, "channels", None) if self.config else None
        channels = (channels_attr if channels_attr else [])[:3]

        if channels:
            for idx, channel in enumerate(channels):
                fallback.append(
                    {
                        "id": f"mock-{idx}",
                        "chat_name": getattr(channel, "name", "Unknown Channel")
                        or "Unknown Channel",
                        "sender": "System",
                        "message": "Monitoring for semantic matches...",
                        "importance": round(0.35 + idx * 0.1, 2),
                        "tags": ["semantic", "watch"],
                        "timestamp": (now - timedelta(minutes=idx * 5)).isoformat(),
                    }
                )
        else:
            fallback.append(
                {
                    "id": "mock-0",
                    "chat_name": "TG Sentinel",
                    "sender": "System",
                    "message": "Activity feed unavailable. Redis offline?",
                    "importance": 0.2,
                    "tags": ["status"],
                    "timestamp": now.isoformat(),
                }
            )
        return fallback

    def load_alerts(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Load recent alerts from Sentinel API.

        Args:
            limit: Maximum number of alerts to load

        Returns:
            List of alert dictionaries
        """
        try:
            import requests

            sentinel_api_url = os.getenv(
                "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
            )

            # Fetch alerts from Sentinel API
            response = requests.get(
                f"{sentinel_api_url}/alerts", params={"limit": limit}, timeout=5
            )

            if not response.ok:
                logger.error(
                    f"Failed to fetch alerts from Sentinel API: {response.status_code}"
                )
                return []

            data = response.json()
            if data.get("status") != "ok" or not data.get("data"):
                logger.error(f"Invalid response from Sentinel API: {data}")
                return []

            alerts = data["data"].get("alerts", [])

            # Get alert mode from config
            alerts_config = (
                getattr(self.config, "alerts", None) if self.config else None
            )
            mode = getattr(alerts_config, "mode", "dm") if alerts_config else "dm"

            # Build chat_id to channel name mapping
            chat_id_to_name = {}
            if self.config and hasattr(self.config, "channels"):
                try:
                    for channel in self.config.channels:
                        if hasattr(channel, "id") and hasattr(channel, "name"):
                            channel_id = channel.id
                            channel_name = channel.name
                            if isinstance(channel_id, (int, str)) and isinstance(
                                channel_name, str
                            ):
                                chat_id_to_name[channel_id] = channel_name
                except Exception:
                    pass

            if alerts:
                result = []
                for alert in alerts:
                    message_text = (alert.get("message_text") or "").strip()
                    triggers = (alert.get("triggers") or "").strip()

                    # Generate better excerpt
                    if message_text:
                        excerpt = self._truncate(message_text, limit=80)
                    elif triggers and triggers.startswith("media-"):
                        # Media message without text
                        media_type = triggers.replace("media-", "").replace(
                            "MessageMedia", ""
                        )
                        excerpt = f"[{media_type}]"
                    else:
                        excerpt = f"Message #{alert['message_id']}"

                    result.append(
                        {
                            "chat_id": alert["chat_id"],
                            "chat_name": (
                                (alert.get("chat_title") or "").strip()
                                or chat_id_to_name.get(alert["chat_id"])
                                or f"Chat {alert['chat_id']}"
                            ),
                            "sender": (alert.get("sender_name") or "").strip()
                            or "Unknown sender",
                            "message_text": message_text,
                            "excerpt": excerpt,
                            "msg_id": alert["message_id"],
                            "score": round(float(alert.get("score", 0.0)), 2),
                            "trigger": triggers or "threshold",
                            "sent_to": mode,
                            "created_at": self._format_timestamp(
                                alert.get("timestamp", "")
                            ),
                        }
                    )
                return result

            return [
                {
                    "chat_id": -1,
                    "chat_name": "No Alerts Yet",
                    "sender": "",
                    "excerpt": "Alerts will appear here once heuristics fire.",
                    "score": 0.0,
                    "trigger": "",
                    "sent_to": mode,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ]

        except Exception as e:
            logger.error(f"Failed to load alerts: {e}", exc_info=True)
            return [
                {
                    "chat_id": -1,
                    "chat_name": "Error Loading Alerts",
                    "sender": "",
                    "excerpt": f"Failed to fetch alerts: {str(e)}",
                    "score": 0.0,
                    "trigger": "",
                    "sent_to": "dm",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ]

    def load_digests(self, limit: int = 14) -> List[Dict[str, Any]]:
        """Load digest statistics from Sentinel API.

        Args:
            limit: Number of days to load

        Returns:
            List of daily digest dictionaries
        """
        try:
            sentinel_api_url = os.getenv(
                "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
            )
            response = requests.get(
                f"{sentinel_api_url}/digests", params={"limit": limit}, timeout=5
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "ok" and data.get("data", {}).get("digests"):
                return data["data"]["digests"]

            # Return empty list if no digests
            return []

        except requests.RequestException as e:
            logger.warning(f"Failed to fetch digests from Sentinel API: {e}")
            # Return empty list on error
            return []

    def load_digest_schedules(self) -> List[Dict[str, Any]]:
        """Load all configured digest schedules from Sentinel API.

        Returns:
            List of digest schedule dictionaries with profile info
        """
        try:
            sentinel_api_url = os.getenv(
                "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
            )
            response = requests.get(f"{sentinel_api_url}/digest/schedules", timeout=5)
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "ok":
                return data.get("data", {}).get("schedules", [])

            return []

        except requests.RequestException as e:
            logger.warning(f"Failed to fetch digest schedules from Sentinel API: {e}")
            return []

    def load_profile_digest_config(self, profile_id: str) -> Dict[str, Any]:
        """Load digest configuration for a specific profile from Sentinel API.

        Args:
            profile_id: Profile identifier

        Returns:
            Dictionary with profile's digest configuration
        """
        try:
            sentinel_api_url = os.getenv(
                "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
            )
            response = requests.get(
                f"{sentinel_api_url}/digest/schedules/{profile_id}", timeout=5
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "ok":
                return data.get("data", {})

            return {}

        except requests.RequestException as e:
            logger.warning(
                f"Failed to fetch digest config for {profile_id} from Sentinel API: {e}"
            )
            return {}
