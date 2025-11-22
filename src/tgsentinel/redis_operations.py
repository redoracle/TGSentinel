"""Redis operations manager for TG Sentinel.

This module centralizes all Redis operations to provide a clean interface
for caching, status updates, and inter-service communication.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from redis import Redis

# Redis key constants
WORKER_STATUS_KEY = "tgsentinel:worker_status"
USER_INFO_KEY = "tgsentinel:user_info"
CREDENTIALS_SENTINEL_KEY = "tgsentinel:credentials:sentinel"
CREDENTIALS_UI_KEY = "tgsentinel:credentials:ui"
LOGIN_PROGRESS_KEY = "tgsentinel:login_progress"
LOGOUT_PROGRESS_KEY = "tgsentinel:logout_progress"
RELOGIN_KEY = "tgsentinel:relogin"
SESSION_UPDATED_CHANNEL = "tgsentinel:session_updated"
CONFIG_UPDATED_CHANNEL = "tgsentinel:config_updated"
CACHE_READY_KEY = "tgsentinel:cache_ready"
CACHED_CHANNELS_KEY = "tgsentinel:cached_channels"
CACHED_USERS_KEY = "tgsentinel:cached_users"


class RedisManager:
    """Manager for all Redis operations in TG Sentinel."""

    def __init__(self, redis_client: Redis):
        """Initialize Redis manager.

        Args:
            redis_client: Connected Redis client instance
        """
        self.redis = redis_client
        self.log = logging.getLogger(__name__)

        # Lua script for atomic TTL refresh without value change
        # If key exists: refresh TTL and return current value
        # If key missing: set default value with TTL and return it
        self._ttl_refresh_script = self.redis.register_script(
            """
            local key = KEYS[1]
            local ttl = tonumber(ARGV[1])
            local default_value = ARGV[2]
            
            local current = redis.call('GET', key)
            if current then
                -- Key exists: refresh TTL and return current value
                redis.call('EXPIRE', key, ttl)
                return current
            else
                -- Key missing: set default with TTL
                redis.call('SETEX', key, ttl, default_value)
                return default_value
            end
        """
        )

    def publish_worker_status(
        self,
        authorized: bool,
        status: str = "authorized",
        ttl: int = 3600,
        extra_fields: Dict[str, Any] | None = None,
    ) -> None:
        """Publish worker authorization status to Redis.

        Args:
            authorized: Whether worker is authorized
            status: Status string (e.g., "authorized", "warming_caches", "ready", "logging_out", "idle")
            ttl: Time-to-live in seconds (default: 1 hour, 0 = no expiry)
            extra_fields: Additional fields to include (e.g., user_id, session_generation)
        """
        try:
            payload = {
                "authorized": authorized,
                "status": status,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            if extra_fields:
                payload.update(extra_fields)

            if ttl > 0:
                self.redis.setex(WORKER_STATUS_KEY, ttl, json.dumps(payload))
            else:
                self.redis.set(WORKER_STATUS_KEY, json.dumps(payload))
            self.log.debug("Published worker status: %s", status)
        except Exception as exc:
            self.log.warning("Failed to publish worker status: %s", exc)

    def refresh_worker_status_ttl(self, ttl: int = 3600) -> Dict[str, Any] | None:
        """Atomically refresh worker status TTL without changing the value.

        This method uses a Lua script to prevent TOCTOU races. If the key exists,
        it extends the TTL and returns the current value. If missing, it sets a
        default authorized status.

        Args:
            ttl: Time-to-live in seconds (default: 1 hour)

        Returns:
            Current worker status dict, or None on error
        """
        try:
            # Default value if key doesn't exist
            default_payload = {
                "authorized": True,
                "status": "authorized",
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            default_json = json.dumps(default_payload)

            # Execute atomic TTL refresh
            result = self._ttl_refresh_script(
                keys=[WORKER_STATUS_KEY], args=[ttl, default_json]
            )

            if result:
                # Parse and return current status
                # Result from script is already a string (decode_responses=True)
                status_data = json.loads(
                    result if isinstance(result, str) else str(result)
                )
                self.log.debug(
                    "[HEARTBEAT] Worker status TTL refreshed atomically (status=%s)",
                    status_data.get("status", "unknown"),
                )
                return status_data
            return None

        except Exception as exc:
            self.log.warning("Failed to refresh worker status TTL: %s", exc)
            return None

    def cache_user_info(
        self, user_data: Dict[str, Any], ttl: Optional[int] = None
    ) -> None:
        """Cache user identity information in Redis.

        Args:
            user_data: Dictionary with user info (username, first_name, etc.)
            ttl: Optional time-to-live in seconds (None = no expiry)
        """
        try:
            if ttl:
                self.redis.setex(USER_INFO_KEY, ttl, json.dumps(user_data))
            else:
                self.redis.set(USER_INFO_KEY, json.dumps(user_data))
            self.log.debug("Cached user info for user_id=%s", user_data.get("user_id"))
        except Exception as exc:
            self.log.warning("Failed to cache user info: %s", exc)

    def get_user_info(self) -> Optional[Dict[str, Any]]:
        """Retrieve cached user info from Redis.

        Returns:
            User info dictionary or None if not found
        """
        try:
            raw = self.redis.get(USER_INFO_KEY)
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode()
            return json.loads(str(raw))
        except Exception as exc:
            self.log.warning("Failed to get user info: %s", exc)
            return None

    def publish_credentials(
        self, fingerprint: Dict[str, str], source: str = "sentinel", ttl: int = 3600
    ) -> None:
        """Publish credential fingerprint to Redis.

        Args:
            fingerprint: Credential fingerprint dictionary
            source: Source of credentials ("sentinel" or "ui")
            ttl: Time-to-live in seconds
        """
        try:
            payload = {
                "fingerprint": fingerprint,
                "source": source,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            key = (
                CREDENTIALS_SENTINEL_KEY if source == "sentinel" else CREDENTIALS_UI_KEY
            )
            self.redis.set(key, json.dumps(payload), ex=ttl)
            self.log.debug("Published %s credentials", source)
        except Exception as exc:
            self.log.warning("Failed to publish %s credentials: %s", source, exc)

    def get_credentials(self, source: str = "ui") -> Optional[Dict[str, Any]]:
        """Retrieve credential fingerprint from Redis.

        Args:
            source: Source to retrieve ("ui" or "sentinel")

        Returns:
            Credentials payload or None if not found
        """
        try:
            key = CREDENTIALS_UI_KEY if source == "ui" else CREDENTIALS_SENTINEL_KEY
            raw = self.redis.get(key)
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode()
            return json.loads(str(raw))
        except Exception as exc:
            self.log.warning("Failed to get %s credentials: %s", source, exc)

    def set_digest_schedule_time(
        self, schedule: str, timestamp: str, ttl: int = 86400 * 7
    ) -> None:
        """Store the last run time for a digest schedule.

        Args:
            schedule: Schedule name (e.g., 'hourly', 'daily')
            timestamp: ISO format timestamp
            ttl: Time-to-live in seconds (default: 7 days)
        """
        try:
            key = f"tgsentinel:digest:last_run:{schedule}"
            self.redis.setex(key, ttl, timestamp)
            self.log.debug("Stored last run time for %s schedule", schedule)
        except Exception as exc:
            self.log.warning(
                "Failed to store digest schedule time for %s: %s", schedule, exc
            )

    def get_digest_schedule_time(self, schedule: str) -> Optional[str]:
        """Retrieve the last run time for a digest schedule.

        Args:
            schedule: Schedule name (e.g., 'hourly', 'daily')

        Returns:
            ISO format timestamp or None if not found
        """
        try:
            key = f"tgsentinel:digest:last_run:{schedule}"
            raw = self.redis.get(key)
            if not raw:
                return None
            if isinstance(raw, bytes):
                return raw.decode()
            return str(raw)
        except Exception as exc:
            self.log.warning(
                "Failed to get digest schedule time for %s: %s", schedule, exc
            )
            return None

    def get_all_digest_schedule_times(self) -> Dict[str, Optional[str]]:
        """Retrieve all digest schedule last run times.

        Returns:
            Dictionary mapping schedule name to ISO timestamp (or None)
        """
        schedules = ["hourly", "every_4h", "every_6h", "every_12h", "daily", "weekly"]
        result = {}
        for schedule in schedules:
            result[schedule] = self.get_digest_schedule_time(schedule)
        return result

    def publish_login_progress(
        self, stage: str, percent: int, message: str, ttl: Optional[int] = 300
    ) -> None:
        """Publish login progress update.

        Args:
            stage: Current stage (e.g., "connecting", "authenticating", "completed")
            percent: Progress percentage (0-100)
            message: Human-readable progress message
            ttl: Time-to-live in seconds (default: 300s to prevent stale data)
        """
        try:
            payload = {
                "stage": stage,
                "percent": percent,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            # Always use TTL to prevent stale progress data between sessions
            # Ensure ttl is not None before calling setex
            ttl_value = ttl if ttl is not None else 300
            self.redis.setex(LOGIN_PROGRESS_KEY, ttl_value, json.dumps(payload))
            self.log.debug(
                "Published login progress: %s (%d%%), TTL=%ds",
                stage,
                percent,
                ttl_value,
            )
        except Exception as exc:
            self.log.debug("Failed to publish login progress: %s", exc)

    def publish_logout_progress(
        self, stage: str, percent: int, message: str, ttl: int = 30
    ) -> None:
        """Publish logout progress update.

        Args:
            stage: Current stage (e.g., "disconnecting", "cleaning", "completed")
            percent: Progress percentage (0-100)
            message: Human-readable progress message
            ttl: Time-to-live in seconds
        """
        try:
            payload = {
                "stage": stage,
                "percent": percent,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.redis.setex(LOGOUT_PROGRESS_KEY, ttl, json.dumps(payload))
            self.log.debug("Published logout progress: %s (%d%%)", stage, percent)
        except Exception as exc:
            self.log.debug("Failed to publish logout progress: %s", exc)

    def publish_session_event(self, event: str, **kwargs: Any) -> None:
        """Publish session update event via pub/sub.

        Args:
            event: Event name (e.g., "session_authorized", "session_imported")
            **kwargs: Additional event data
        """
        try:
            payload = {
                "event": event,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **kwargs,
            }
            self.redis.publish(SESSION_UPDATED_CHANNEL, json.dumps(payload))
            self.log.info("Published session event: %s", event)
        except Exception as exc:
            self.log.error("Failed to publish session event: %s", exc)

    def publish_config_event(
        self, event: str, config_keys: List[str] | None = None, **kwargs: Any
    ) -> None:
        """Publish config update event via pub/sub.

        Args:
            event: Event name (e.g., "config_reloaded", "config_updated")
            config_keys: List of config keys that were updated
            **kwargs: Additional event data
        """
        try:
            payload = {
                "event": event,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "config_keys": config_keys or [],
                **kwargs,
            }
            self.redis.publish(CONFIG_UPDATED_CHANNEL, json.dumps(payload))
            self.log.info("Published config event: %s (keys: %s)", event, config_keys)
        except Exception as exc:
            self.log.error("Failed to publish config event: %s", exc)

    def clear_user_session_data(self) -> None:
        """Clear all user session data from Redis (used during logout)."""
        keys_to_clear = [
            USER_INFO_KEY,
            CACHED_CHANNELS_KEY,
            CACHED_USERS_KEY,
            CACHE_READY_KEY,
            LOGIN_PROGRESS_KEY,
        ]

        try:
            for key in keys_to_clear:
                self.redis.delete(key)
            self.log.info("Cleared user session data from Redis")
        except Exception as exc:
            self.log.warning("Failed to clear session data: %s", exc)

    def clear_participant_requests(
        self, pattern: str = "tgsentinel:participant_request:*"
    ) -> int:
        """Clear participant request keys from Redis.

        Args:
            pattern: Key pattern to delete

        Returns:
            Number of keys deleted
        """
        try:
            keys_to_delete = list(self.redis.scan_iter(pattern))
            if keys_to_delete:
                deleted = self.redis.delete(*keys_to_delete)
                # Redis delete returns int, cast to int to satisfy type checker
                deleted_count = int(deleted) if isinstance(deleted, (int, str)) else 0
                self.log.debug("Cleared %d participant request keys", deleted_count)
                return deleted_count
            return 0
        except Exception as exc:
            self.log.warning("Failed to clear participant requests: %s", exc)
            return 0

    def cache_avatar(
        self, entity_id: int, avatar_b64: str, is_user: bool = True
    ) -> None:
        """Cache avatar as base64 string in Redis.

        Args:
            entity_id: Telegram entity ID
            avatar_b64: Base64-encoded avatar data
            is_user: Whether entity is a user (vs channel)
        """
        try:
            prefix = "user" if is_user else "chat"
            # Use format that matches UI expectations: tgsentinel:{prefix}_avatar:{entity_id}
            redis_key = f"tgsentinel:{prefix}_avatar:{entity_id}"
            self.redis.set(redis_key, avatar_b64)  # No TTL
            self.log.debug("Cached avatar for %s %d", prefix, entity_id)
        except Exception as exc:
            self.log.debug("Failed to cache avatar: %s", exc)

    def set_cache_ready(self, ready: bool = True, ttl: int = 86400) -> None:
        """Set cache ready flag in Redis.

        Args:
            ready: Whether cache is ready
            ttl: Time-to-live in seconds (default: 24 hours)
        """
        try:
            if ready:
                self.redis.setex(CACHE_READY_KEY, ttl, "1")
            else:
                self.redis.delete(CACHE_READY_KEY)
            self.log.debug("Set cache_ready=%s", ready)
        except Exception as exc:
            self.log.warning("Failed to set cache ready flag: %s", exc)

    def is_cache_ready(self) -> bool:
        """Check if cache is ready.

        Returns:
            True if cache is ready, False otherwise
        """
        try:
            return bool(self.redis.get(CACHE_READY_KEY))
        except Exception as exc:
            self.log.warning("Failed to check cache ready: %s", exc)
            return False

    def cache_channels(self, channels: List[Dict[str, Any]], ttl: int = 86400) -> None:
        """Cache channel list in Redis.

        Args:
            channels: List of channel dictionaries
            ttl: Time-to-live in seconds (default: 24 hours)
        """
        try:
            self.redis.setex(CACHED_CHANNELS_KEY, ttl, json.dumps(channels))
            self.log.debug("Cached %d channels", len(channels))
        except Exception as exc:
            self.log.warning("Failed to cache channels: %s", exc)

    def get_cached_channels(self) -> Optional[List[Dict[str, Any]]]:
        """Retrieve cached channels from Redis.

        Returns:
            List of channel dictionaries or None if not cached
        """
        try:
            raw = self.redis.get(CACHED_CHANNELS_KEY)
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode()
            return json.loads(str(raw))
        except Exception as exc:
            self.log.warning("Failed to get cached channels: %s", exc)
            return None

    def cache_users(self, users: List[Dict[str, Any]], ttl: int = 86400) -> None:
        """Cache user list in Redis.

        Args:
            users: List of user dictionaries
            ttl: Time-to-live in seconds (default: 24 hours)
        """
        try:
            self.redis.setex(CACHED_USERS_KEY, ttl, json.dumps(users))
            self.log.debug("Cached %d users", len(users))
        except Exception as exc:
            self.log.warning("Failed to cache users: %s", exc)

    def get_cached_users(self) -> Optional[List[Dict[str, Any]]]:
        """Retrieve cached users from Redis.

        Returns:
            List of user dictionaries or None if not cached
        """
        try:
            raw = self.redis.get(CACHED_USERS_KEY)
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode()
            return json.loads(str(raw))
        except Exception as exc:
            self.log.warning("Failed to get cached users: %s", exc)
            return None

    def get_relogin_state(self) -> Optional[Dict[str, Any]]:
        """Get relogin handshake state from Redis.

        Returns:
            Relogin state dictionary or None
        """
        try:
            raw = self.redis.get(RELOGIN_KEY)
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode()
            return json.loads(str(raw))
        except Exception as exc:
            self.log.warning("Failed to get relogin state: %s", exc)
            return None

    def clear_relogin_state(self) -> None:
        """Clear relogin handshake marker from Redis."""
        try:
            self.redis.delete(RELOGIN_KEY)
            self.log.info("Cleared relogin handshake marker")
        except Exception as exc:
            self.log.warning("Failed to clear relogin state: %s", exc)

    def set_auth_response(
        self,
        auth_response_hash: str,
        request_id: str,
        payload: Dict[str, Any],
        ttl: int = 120,
    ) -> None:
        """Store auth response in Redis hash.

        Args:
            auth_response_hash: Redis hash key for auth responses
            request_id: Request identifier
            payload: Response payload
            ttl: Time-to-live for the hash in seconds
        """
        try:
            self.redis.hset(auth_response_hash, request_id, json.dumps(payload))
            self.redis.expire(auth_response_hash, ttl)
        except Exception as exc:
            self.log.warning("Failed to store auth response: %s", exc)

    def scan_and_get_requests(self, pattern: str) -> List[tuple[str, Dict[str, Any]]]:
        """Scan for request keys and retrieve their data.

        Args:
            pattern: Redis key pattern to scan for

        Returns:
            List of tuples (key, request_data_dict)
        """
        results = []
        try:
            for key in self.redis.scan_iter(pattern):
                try:
                    # Decode key if it's bytes
                    if isinstance(key, bytes):
                        key = key.decode()

                    request_data = self.redis.get(key)
                    if not request_data:
                        continue

                    # Ensure request_data is a string
                    if isinstance(request_data, bytes):
                        request_data = request_data.decode()

                    req = json.loads(str(request_data))
                    results.append((key, req))
                except Exception as exc:
                    self.log.debug("Failed to parse request key %s: %s", key, exc)
        except Exception as exc:
            self.log.warning("Failed to scan for pattern %s: %s", pattern, exc)

        return results

    def set_response_with_ttl(
        self, response_key: str, response_data: Dict[str, Any], ttl: int = 60
    ) -> None:
        """Set a response key with TTL.

        Args:
            response_key: Redis key for the response
            response_data: Response data dictionary
            ttl: Time-to-live in seconds
        """
        try:
            self.redis.setex(response_key, ttl, json.dumps(response_data))
            self.log.debug("Stored response at key=%s", response_key)
        except Exception as exc:
            self.log.warning("Failed to set response %s: %s", response_key, exc)

    def delete_request_key(self, key: str) -> None:
        """Delete a request key from Redis.

        Args:
            key: Redis key to delete
        """
        try:
            self.redis.delete(key)
        except Exception as exc:
            self.log.debug("Failed to delete key %s: %s", key, exc)
