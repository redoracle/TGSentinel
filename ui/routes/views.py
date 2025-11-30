"""HTML view routes for TG Sentinel UI pages.

This blueprint handles all page rendering (HTML templates):
- Dashboard (homepage)
- Alerts
- Configuration
- Analytics
- Profiles
- Developer tools
- Console
- Documentation
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import requests
from flask import Blueprint, redirect, render_template, request, url_for

try:
    from ui.core import get_deps
except ImportError:
    from core import get_deps  # type: ignore

logger = logging.getLogger(__name__)

# Create blueprint
views_bp = Blueprint("views", __name__)

# Import psutil for health metrics
try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

try:
    import redis
except ImportError:
    redis = None  # type: ignore


def _load_local_config_fallback(deps):
    """Load configuration from local deps.config as fallback.

    Args:
        deps: Dependencies object with config attribute

    Returns:
        Tuple of (session_path, interests, monitored_users, channels)
    """
    session_path = getattr(deps.config, "telegram_session", "") if deps.config else ""
    interests_attr = getattr(deps.config, "interests", None) if deps.config else None
    interests = list(interests_attr) if interests_attr is not None else []
    monitored_users_attr = (
        getattr(deps.config, "monitored_users", None) if deps.config else None
    )
    monitored_users = (
        list(monitored_users_attr) if monitored_users_attr is not None else []
    )

    try:
        from ui.utils.serializers import serialize_channels
    except ImportError:
        from utils.serializers import serialize_channels  # type: ignore
    channels = serialize_channels(deps.config)

    return session_path, interests, monitored_users, channels


@views_bp.route("/")
def dashboard():
    """Dashboard homepage."""
    deps = get_deps()
    return render_template(
        "dashboard.html",
        summary=deps.data_service.compute_summary(),
        activity=deps.data_service.load_live_feed(limit=10),
        health=deps.data_service.compute_health(psutil=psutil, redis_module=redis),
        recent_alerts=deps.data_service.load_alerts(limit=8),
    )


@views_bp.route("/alerts")
def alerts():
    """Alerts page (legacy route - redirects to feeds)."""
    return redirect(url_for("views.feeds"))


@views_bp.route("/feeds")
def feeds():
    """Feeds page showing both alerts and interests."""
    deps = get_deps()
    return render_template(
        "feeds.html",
        alerts=deps.data_service.load_alerts(limit=50),
        interests=deps.data_service.load_interests(limit=50),
        digests=deps.data_service.load_digests(),
    )


@views_bp.route("/config")
def config():
    """Configuration page - fetches from Sentinel API (single source of truth)."""
    deps = get_deps()

    # Fetch config from Sentinel API
    sentinel_api_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")
    channels = []
    monitored_users = []
    interests = []
    session_path = ""

    try:
        response = requests.get(f"{sentinel_api_url}/config", timeout=5)
        if response.ok:
            config_data = response.json().get("data", {})
            raw_channels = config_data.get("channels", [])

            # Normalize channels to include chat_id for template compatibility
            for ch in raw_channels:
                if isinstance(ch, dict):
                    ch["chat_id"] = ch.get("id", ch.get("chat_id", 0))
                    if "enabled" not in ch:
                        ch["enabled"] = True

            channels = raw_channels
            monitored_users = config_data.get("monitored_users", [])
            interests = config_data.get("interests", [])
            session_path = config_data.get("telegram", {}).get("session", "")
            logger.info(
                f"Loaded config from Sentinel: {len(channels)} channels, {len(monitored_users)} users"
            )
        else:
            logger.warning(
                f"Failed to fetch config from Sentinel: {response.status_code}"
            )
            # Fallback to local config if Sentinel unavailable
            session_path, interests, monitored_users, channels = (
                _load_local_config_fallback(deps)
            )
    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API: {e}")
        # Fallback to local config
        session_path, interests, monitored_users, channels = (
            _load_local_config_fallback(deps)
        )

    # Define heuristic options - centralized list that stays in sync with backend
    heuristic_options = ["Mentions", "VIP", "Keywords", "Reaction Surge", "Replies"]

    return render_template(
        "config.html",
        session_path=session_path,
        channels=channels,
        monitored_users=monitored_users,
        interests=interests,
        heuristic_options=heuristic_options,
        summary=deps.data_service.compute_summary(),
    )


@views_bp.route("/analytics")
def analytics():
    """Analytics page."""
    deps = get_deps()
    return render_template(
        "analytics.html",
        summary=deps.data_service.compute_summary(),
        health=deps.data_service.compute_health(psutil=psutil, redis_module=redis),
    )


@views_bp.route("/profiles")
def profiles():
    """Interest and alert profiles management page."""
    deps = get_deps()
    # Load interest profiles from the persistence layer
    profiles = deps.profile_service.load_profiles() if deps.profile_service else {}

    # Also include legacy interests from config for backward compatibility
    interests_attr = getattr(deps.config, "interests", None) if deps.config else None
    legacy_interests = list(interests_attr) if interests_attr is not None else []

    # Merge: persisted profiles take precedence, then add legacy interests not in profiles
    profile_names = set(profiles.keys())
    for legacy_name in legacy_interests:
        if legacy_name not in profile_names:
            profile_names.add(legacy_name)

    # Load alert profiles (heuristic/keyword-based)
    # First check if we have persisted alert profiles
    alert_profiles = (
        deps.profile_service.load_alert_profiles() if deps.profile_service else {}
    )

    # If no persisted profiles, auto-migrate from config channels
    if not alert_profiles and deps.config:
        try:
            channels_attr = getattr(deps.config, "channels", None)
            if channels_attr:
                for channel in channels_attr:
                    channel_id = getattr(channel, "id", None)
                    if channel_id:
                        profile_id = f"channel_{channel_id}"
                        alert_profiles[profile_id] = {
                            "id": profile_id,
                            "name": getattr(channel, "name", f"Channel {channel_id}"),
                            "type": "channel",
                            "channel_id": channel_id,
                            "enabled": True,
                            "vip_senders": getattr(channel, "vip_senders", []),
                            "excluded_users": getattr(channel, "excluded_users", []),
                            "keywords": getattr(channel, "keywords", []),
                            "action_keywords": getattr(channel, "action_keywords", []),
                            "decision_keywords": getattr(
                                channel, "decision_keywords", []
                            ),
                            "urgency_keywords": getattr(
                                channel, "urgency_keywords", []
                            ),
                            "importance_keywords": getattr(
                                channel, "importance_keywords", []
                            ),
                            "release_keywords": getattr(
                                channel, "release_keywords", []
                            ),
                            "security_keywords": getattr(
                                channel, "security_keywords", []
                            ),
                            "risk_keywords": getattr(channel, "risk_keywords", []),
                            "opportunity_keywords": getattr(
                                channel, "opportunity_keywords", []
                            ),
                            "reaction_threshold": getattr(
                                channel, "reaction_threshold", 5
                            ),
                            "reply_threshold": getattr(channel, "reply_threshold", 3),
                            "detect_codes": getattr(channel, "detect_codes", False),
                            "detect_documents": getattr(
                                channel, "detect_documents", False
                            ),
                            "prioritize_pinned": getattr(
                                channel, "prioritize_pinned", False
                            ),
                            "prioritize_admin": getattr(
                                channel, "prioritize_admin", False
                            ),
                            "detect_polls": getattr(channel, "detect_polls", False),
                            "rate_limit_per_hour": getattr(
                                channel, "rate_limit_per_hour", 10
                            ),
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                # Save migrated profiles
                if alert_profiles and deps.profile_service:
                    deps.profile_service.save_alert_profiles(alert_profiles)
                    logger.info(
                        f"Auto-migrated {len(alert_profiles)} alert profiles from config"
                    )
        except Exception as exc:
            logger.error(f"Error auto-migrating alert profiles: {exc}")

    return render_template(
        "profiles.html",
        interests=sorted(profile_names),
        profiles=profiles,
        alert_profiles=alert_profiles,
        alert_channel=os.getenv("NOTIFICATION_CHANNEL", ""),
        embeddings_model=os.getenv("EMBEDDINGS_MODEL", "not configured"),
    )


@views_bp.route("/developer")
def developer():
    """Developer tools page."""
    return render_template("developer.html")


@views_bp.route("/developer/message-formats")
def message_formats():
    """Message formats editor page."""
    return render_template("message_formats.html")


@views_bp.route("/console")
def console():
    """Console page."""
    return render_template("console.html")


@views_bp.route("/docs")
def docs():
    """API documentation page."""
    # Get base URL from environment or construct from request
    api_base_url = os.getenv("API_BASE_URL")
    if not api_base_url:
        # Construct from request context (scheme + host)
        api_base_url = f"{request.scheme}://{request.host}"
    return render_template("docs.html", api_base_url=api_base_url)
