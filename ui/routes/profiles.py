"""Profile management API routes for TG Sentinel UI.

This blueprint handles all profile-related operations:
- Interest profiles (semantic-based scoring)
- Alert profiles (keyword/heuristic-based scoring)
- Import/export operations
- Backtest operations
"""

import json
import logging
import os
from datetime import datetime, timezone

import requests
import yaml
from flask import Blueprint, jsonify, make_response, request

from ui.services.profiles_service import (
    INTEREST_PROFILE_ID_PREFIX,
)

logger = logging.getLogger(__name__)

# Blueprint setup
profiles_bp = Blueprint("profiles", __name__, url_prefix="/api/profiles")

# Global dependencies (injected via init function)
_config = None
_engine = None
_query_one = None
_query_all = None
_profile_service = None


def init_profiles_routes(
    config=None,
    engine=None,
    query_one=None,
    query_all=None,
    profile_service=None,
):
    """Initialize profiles blueprint with dependencies.

    Args:
        config: Application config object
        engine: SQLAlchemy engine
        query_one: Function for single-row queries
        query_all: Function for multi-row queries
        profile_service: ProfileService instance
    """
    global _config, _engine, _query_one, _query_all, _profile_service
    _config = config
    _engine = engine
    _query_one = query_one
    _query_all = query_all
    _profile_service = profile_service
    logger.info("Profiles routes initialized")


# ═══════════════════════════════════════════════════════════════════
# Interest Profile Routes (Semantic-based)
# ═══════════════════════════════════════════════════════════════════


@profiles_bp.get("/export")
def export_profiles():
    """Export interest profiles as YAML file."""
    try:
        interests_attr = getattr(_config, "interests", None) if _config else None
        interests = list(interests_attr) if interests_attr is not None else []

        # Create YAML content
        yaml_content = yaml.dump(
            {"interests": interests},
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

        # Create response with download headers
        response = make_response(yaml_content)
        response.headers["Content-Type"] = "application/x-yaml"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="interests_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")}.yml"'
        )
        return response

    except Exception as exc:
        logger.error(f"Failed to export interests: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@profiles_bp.post("/train")
def train_profile():
    """Queue training for an interest profile."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    topic = payload.get("topic")
    logger.info("Queued interest profile training for '%s'", topic)
    return jsonify({"status": "queued", "topic": topic})


@profiles_bp.post("/test")
def test_profile():
    """Test semantic similarity for an interest profile.

    Proxies the request to the Sentinel API which has the embeddings model loaded.
    """
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    # Handle type conversion for sample and interest (may be sent as int or string)
    raw_sample = payload.get("sample", "")
    raw_interest = payload.get("interest", "")

    # Convert to string and strip whitespace
    sample = str(raw_sample).strip() if raw_sample else ""
    interest = str(raw_interest).strip() if raw_interest else ""

    if not sample:
        return jsonify({"status": "error", "message": "Sample text is required"}), 400

    if not interest:
        return (
            jsonify({"status": "error", "message": "Interest/profile is required"}),
            400,
        )

    try:
        # Get Sentinel API base URL
        sentinel_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

        # Forward request to Sentinel API
        response = requests.post(
            f"{sentinel_url}/profiles/interest/test_similarity",
            json={"sample": sample, "profile_id": interest},
            timeout=10,
        )

        if response.status_code != 200:
            logger.error(
                f"Sentinel API returned {response.status_code}: {response.text}"
            )
            return (
                jsonify({"status": "error", "message": "Failed to test similarity"}),
                response.status_code,
            )

        result = response.json()

        # Return simplified response for backward compatibility
        # Include max_sample_similarity if provided by Sentinel
        response_data = {
            "score": result.get("score", 0.0),
            "interpretation": result.get("interpretation", ""),
            "model": result.get("model", "all-MiniLM-L6-v2"),
        }

        # Add max_sample_similarity if available (for exact match detection)
        if "max_sample_similarity" in result:
            response_data["max_sample_similarity"] = result["max_sample_similarity"]

        # Add threshold info for UI display
        if "threshold" in result:
            response_data["threshold"] = result["threshold"]
        if "will_match" in result:
            response_data["will_match"] = result["will_match"]

        return jsonify(response_data)

    except requests.exceptions.RequestException as exc:
        logger.error(f"Error connecting to Sentinel API: {exc}")
        return (
            jsonify(
                {"status": "error", "message": "Could not connect to Sentinel service"}
            ),
            500,
        )
    except Exception as exc:
        logger.error(f"Error testing similarity: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@profiles_bp.post("/toggle")
def toggle_profile():
    """Toggle profile enabled/disabled state."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_name = data.get("name", "").strip()
    enabled = data.get("enabled", True)

    if not profile_name:
        return jsonify({"status": "error", "message": "Profile name required"}), 400

    try:
        profile = (
            _profile_service.get_profile(profile_name) if _profile_service else None
        )
        if profile is None:
            interests_attr = getattr(_config, "interests", None) if _config else None
            legacy_interests = (
                list(interests_attr) if interests_attr is not None else []
            )

            if profile_name in legacy_interests:
                logger.info(
                    f"Auto-creating profile for legacy interest '{profile_name}' during toggle"
                )
                profile = {
                    "name": profile_name,
                    "description": "Migrated from legacy interests",
                    "positive_samples": [],
                    "negative_samples": [],
                    "threshold": 0.42,
                    "weight": 1.0,
                    "enabled": enabled,
                    "priority": "normal",
                    "keywords": [],
                    "channels": [],
                    "tags": [],
                }
            else:
                logger.warning(f"Toggle failed: profile '{profile_name}' not found")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile '{profile_name}' not found",
                        }
                    ),
                    404,
                )
        else:
            profile["enabled"] = enabled

        if not _profile_service or not _profile_service.upsert_profile(profile):
            logger.error(f"Failed to persist toggle for profile '{profile_name}'")
            return (
                jsonify(
                    {"status": "error", "message": "Failed to save profile changes"}
                ),
                500,
            )

        logger.info(
            f"Toggled profile '{profile_name}' -> {'enabled' if enabled else 'disabled'}"
        )
        return jsonify(
            {"status": "success", "profile": profile_name, "enabled": enabled}
        )

    except Exception as exc:
        logger.error(f"Error toggling profile '{profile_name}': {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.post("/interest/backtest")
def backtest_interest_profile():
    """Backtest an interest profile against historical messages."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    # Accept either profile_id or name (prefer profile_id)
    profile_id = payload.get("profile_id")
    profile_name = payload.get("name", "").strip()

    # Safely parse and validate hours_back
    try:
        hours_back_raw = payload.get("hours_back", 24)
        hours_back = int(hours_back_raw)
    except (ValueError, TypeError):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"hours_back must be a valid integer, got: {type(hours_back_raw).__name__}",
                }
            ),
            400,
        )

    # Safely parse and validate max_messages
    try:
        max_messages_raw = payload.get("max_messages", 100)
        max_messages = int(max_messages_raw)
    except (ValueError, TypeError):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"max_messages must be a valid integer, got: {type(max_messages_raw).__name__}",
                }
            ),
            400,
        )

    if not profile_id and not profile_name:
        return (
            jsonify({"status": "error", "message": "Profile ID or name required"}),
            400,
        )

    # Validate hours_back bounds (prevent resource exhaustion)
    if hours_back < 0 or hours_back > 168:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "hours_back must be between 0 and 168 (7 days)",
                }
            ),
            400,
        )

    # Validate max_messages bounds (prevent SQL injection and excessive queries)
    if max_messages < 1 or max_messages > 1000:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "max_messages must be between 1 and 1000",
                }
            ),
            400,
        )

    try:
        # Get profile - use profile_id if available, otherwise search by name
        sentinel_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

        if profile_id:
            # Fetch by ID from Sentinel API
            profile_url = f"{sentinel_url}/profiles/interest/{profile_id}"
            resp = requests.get(profile_url, timeout=10)
            if resp.status_code == 404:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile {profile_id} not found",
                        }
                    ),
                    404,
                )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "ok":
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Failed to fetch profile: {data.get('message')}",
                        }
                    ),
                    500,
                )
            profile = data.get("data")
            if not profile:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile {profile_id} not found",
                        }
                    ),
                    404,
                )
            # Use profile name from the fetched profile
            profile_name = profile.get("name", str(profile_id))
        else:
            # Fetch by name: get all profiles and find matching name
            profiles_url = f"{sentinel_url}/profiles/interest"
            resp = requests.get(profiles_url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "ok":
                return (
                    jsonify({"status": "error", "message": "Failed to fetch profiles"}),
                    500,
                )

            all_profiles = data.get("data", {})
            profile = None
            for pid, pdata in all_profiles.items():
                if pdata.get("name") == profile_name:
                    profile = pdata
                    profile_id = pid
                    break

            if not profile:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile '{profile_name}' not found",
                        }
                    ),
                    404,
                )

        # Call Sentinel API for semantic scoring (model runs in Sentinel container)
        try:
            backtest_url = f"{sentinel_url}/profiles/interest/backtest"

            payload = {
                "profile_name": profile_name,
                "profile": profile,
                "hours_back": hours_back,
                "max_messages": max_messages,
            }

            response = requests.post(
                backtest_url, json=payload, timeout=30
            )  # 30s timeout for semantic processing

            if not response.ok:
                error_data = response.json() if response.text else {}
                error_msg = error_data.get("message", "Sentinel API request failed")
                logger.error(
                    f"Sentinel backtest API error: {response.status_code} - {error_msg}"
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Semantic scoring failed: {error_msg}",
                        }
                    ),
                    response.status_code,
                )

            # Return Sentinel's response directly
            result = response.json()
            logger.info(
                f"Interest profile backtest completed via Sentinel: {result.get('stats', {})}"
            )
            return jsonify(result)

        except requests.exceptions.Timeout:
            logger.error("Sentinel backtest API timeout")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Semantic scoring timeout (>30s). Try reducing hours_back or max_messages.",
                    }
                ),
                504,
            )
        except requests.exceptions.ConnectionError as ce:
            logger.error(f"Sentinel backtest API connection error: {ce}")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Cannot connect to Sentinel service. Is it running?",
                    }
                ),
                503,
            )
        except Exception as exc:
            logger.error(
                f"Unexpected error calling Sentinel backtest: {exc}", exc_info=True
            )
            return (
                jsonify({"status": "error", "message": f"Backtest failed: {str(exc)}"}),
                500,
            )

    except Exception as exc:
        logger.error(f"Error backtesting interest profile: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════
# Alert Profile Routes (Keyword/Heuristic-based)
# ═══════════════════════════════════════════════════════════════════


@profiles_bp.get("/alert/list")
def list_alert_profiles():
    """List all alert profiles with enriched metadata."""
    try:
        profiles = _profile_service.load_alert_profiles() if _profile_service else {}

        # Enrich profiles with activity metadata
        enriched_profiles = []
        for profile in profiles.values():
            enriched = dict(profile)

            # Add activity metadata if database is available
            if _engine and _query_one:
                try:
                    # Get last triggered timestamp for this profile
                    profile_id = enriched.get("id", "")
                    last_triggered = _query_one(
                        """
                        SELECT MAX(created_at) as last_triggered
                        FROM messages
                        WHERE (flagged_for_alerts_feed = 1 OR flagged_for_interest_feed = 1)
                          AND triggers LIKE :profile_pattern
                        """,
                        profile_pattern=f"%{profile_id}%",
                    )

                    if last_triggered and last_triggered.get("last_triggered"):
                        enriched["last_triggered_at"] = last_triggered["last_triggered"]

                    # Get trigger count in last 24 hours
                    trigger_count = _query_one(
                        """
                        SELECT COUNT(*) as count
                        FROM messages
                        WHERE (flagged_for_alerts_feed = 1 OR flagged_for_interest_feed = 1)
                          AND triggers LIKE :profile_pattern
                          AND datetime(created_at) >= datetime('now', '-24 hours')
                        """,
                        profile_pattern=f"%{profile_id}%",
                    )

                    if trigger_count:
                        enriched["recent_triggers"] = trigger_count.get("count", 0)
                except Exception as e:
                    logger.debug(f"Could not enrich profile {profile.get('id')}: {e}")

            enriched_profiles.append(enriched)

        stats = {
            "total": len(enriched_profiles),
            "enabled": sum(1 for p in enriched_profiles if p.get("enabled", True)),
        }
        return jsonify({"status": "ok", "profiles": enriched_profiles, "stats": stats})
    except Exception as exc:
        logger.error(f"Error listing alert profiles: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.get("/alert/get")
def get_alert_profile():
    """Get a specific alert profile."""
    profile_id_str = request.args.get("id", "").strip()
    if not profile_id_str:
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    try:
        profile_id = int(profile_id_str)
        profile = (
            _profile_service.get_alert_profile(profile_id) if _profile_service else None
        )
        if not profile:
            return jsonify({"status": "error", "message": "Profile not found"}), 404
        return jsonify({"status": "ok", "profile": profile})
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid profile ID format"}), 400
    except Exception as exc:
        logger.error(f"Error getting alert profile: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.post("/alert/upsert")
def upsert_alert_profile():
    """Create or update an alert profile."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    profile = request.get_json(silent=True)
    if not profile:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    # Validate profile name is present
    profile_name = profile.get("name", "").strip()
    if not profile_name:
        return jsonify({"status": "error", "message": "Profile name required"}), 400

    # Convert ID to integer if provided (for updates)
    if "id" in profile and profile["id"] is not None:
        try:
            profile["id"] = int(profile["id"])
        except (ValueError, TypeError):
            return (
                jsonify({"status": "error", "message": "Invalid profile ID format"}),
                400,
            )

    # Add timestamps
    now = datetime.now(timezone.utc).isoformat()
    if "created_at" not in profile:
        profile["created_at"] = now
    profile["updated_at"] = now

    try:
        if not _profile_service or not _profile_service.upsert_alert_profile(profile):
            return (
                jsonify({"status": "error", "message": "Failed to save profile"}),
                500,
            )

        # Get the ID that was assigned/used
        profile_id = profile.get("id")

        # Note: No sync needed - save_alert_profiles() already sends to Sentinel API
        # which writes directly to config/profiles_alert.yml

        logger.info(f"Alert profile upserted: {profile_id}")
        return jsonify({"status": "ok", "profile_id": profile_id})
    except Exception as exc:
        logger.error(f"Error upserting alert profile: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.delete("/alert/delete")
def delete_alert_profile():
    """Delete an alert profile."""
    profile_id_str = request.args.get("id", "").strip()
    if not profile_id_str:
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    try:
        profile_id = int(profile_id_str)
        if not _profile_service or not _profile_service.delete_alert_profile(
            profile_id
        ):
            return jsonify({"status": "error", "message": "Profile not found"}), 404

        # Sync to config file
        if _profile_service:
            _profile_service.sync_alert_profiles_to_config(
                _profile_service.tgsentinel_config
            )

        logger.info(f"Alert profile deleted: {profile_id}")
        return jsonify({"status": "ok", "message": "Profile deleted"})
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid profile ID format"}), 400
    except Exception as exc:
        logger.error(f"Error deleting alert profile: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.post("/alert/toggle")
def toggle_alert_profile():
    """Toggle alert profile enabled status via Sentinel API."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_id_raw = payload.get("id")

    if profile_id_raw is None or profile_id_raw == "":
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    try:
        profile_id = int(profile_id_raw)

        # Use dedicated toggle method that calls Sentinel API
        if not _profile_service or not _profile_service.toggle_alert_profile(
            profile_id
        ):
            return (
                jsonify({"status": "error", "message": "Failed to toggle profile"}),
                500,
            )

        # Fetch updated profile to return current state
        profile = (
            _profile_service.get_alert_profile(profile_id) if _profile_service else None
        )
        enabled = profile.get("enabled", False) if profile else False

        logger.info(
            f"Alert profile {profile_id} {'enabled' if enabled else 'disabled'}"
        )
        return jsonify({"status": "ok", "enabled": enabled})
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid profile ID format"}), 400
    except Exception as exc:
        logger.error(f"Error toggling alert profile: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.post("/alert/backtest")
def backtest_alert_profile():
    """Backtest an alert profile against historical messages.

    This endpoint proxies the request to the Sentinel API, which owns the
    message database and heuristics scoring pipeline.
    """
    logger.info("[BACKTEST] Alert backtest endpoint called")

    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_id = payload.get("id", "").strip()
    if not profile_id:
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    # Forward the request to Sentinel API
    try:
        sentinel_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")
        backtest_url = f"{sentinel_url}/profiles/alert/backtest"

        # Build request payload for Sentinel
        sentinel_payload = {
            "profile_id": profile_id,
            "hours_back": payload.get("hours_back", 24),
            "max_messages": payload.get("max_messages", 100),
        }

        # Add optional channel filter if provided
        if "channel_filter" in payload:
            sentinel_payload["channel_filter"] = payload["channel_filter"]

        logger.info(
            f"[BACKTEST] Forwarding request to Sentinel: {backtest_url}, "
            f"profile_id={profile_id}, hours_back={sentinel_payload['hours_back']}"
        )

        # Make request to Sentinel API
        response = requests.post(
            backtest_url,
            json=sentinel_payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )

        # Check for HTTP errors
        response.raise_for_status()

        # Parse and return the response
        result = response.json()
        logger.info(
            f"[BACKTEST] Received response from Sentinel: "
            f"status={result.get('status')}, "
            f"matched_messages={result.get('stats', {}).get('matched_messages', 0)}"
        )

        return jsonify(result)

    except requests.exceptions.Timeout:
        logger.error("[BACKTEST] Timeout connecting to Sentinel API")
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Request to Sentinel API timed out",
                }
            ),
            504,
        )
    except requests.exceptions.ConnectionError as exc:
        logger.error(f"[BACKTEST] Connection error to Sentinel API: {exc}")
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Could not connect to Sentinel API",
                }
            ),
            503,
        )
    except requests.exceptions.HTTPError as exc:
        logger.error(f"[BACKTEST] HTTP error from Sentinel API: {exc}")
        status_code = exc.response.status_code if exc.response else 500
        error_msg = "Sentinel API returned an error"
        try:
            error_data = exc.response.json()
            error_msg = error_data.get("message", error_msg)
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
            pass
        return jsonify({"status": "error", "message": error_msg}), status_code
    except Exception as exc:
        logger.error(
            f"[BACKTEST] Error backtesting alert profile: {exc}", exc_info=True
        )
        return jsonify({"status": "error", "message": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════
# Interest Profile CRUD Routes (Proxy to Sentinel API)
# ═══════════════════════════════════════════════════════════════════


@profiles_bp.get("/interest/list")
def list_interest_profiles():
    """List all interest profiles from Sentinel API.

    Returns:
        JSON with list of all interest profiles.
    """
    try:
        if not _profile_service:
            return (
                jsonify(
                    {"status": "error", "message": "ProfileService not initialized"}
                ),
                500,
            )

        profiles = _profile_service.load_profiles()

        # Convert to list format for UI
        # Profiles from Sentinel API already have 'id' field, so we don't need to add it
        profiles_list = [profile for profile_id, profile in profiles.items()]

        return jsonify({"status": "ok", "profiles": profiles_list})

    except Exception as exc:
        logger.error(f"Failed to list interest profiles: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@profiles_bp.get("/interest/<int:profile_id>")
def get_interest_profile(profile_id: int):
    """Get a single interest profile by ID.

    Args:
        profile_id: Interest profile ID (3000-3999)

    Returns:
        JSON with profile data or error.
    """
    try:
        if not _profile_service:
            return (
                jsonify(
                    {"status": "error", "message": "ProfileService not initialized"}
                ),
                500,
            )

        # Validate ID range
        if not (3000 <= profile_id < 4000):
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Invalid interest profile ID: {profile_id}. Must be 3000-3999.",
                    }
                ),
                400,
            )

        profile = _profile_service.get_profile(str(profile_id))

        if profile is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Interest profile {profile_id} not found",
                    }
                ),
                404,
            )

        return jsonify({"status": "ok", "profile": profile})

    except Exception as exc:
        logger.error(
            f"Failed to get interest profile {profile_id}: {exc}", exc_info=True
        )
        return jsonify({"status": "error", "message": str(exc)}), 500


@profiles_bp.post("/interest/upsert")
def upsert_interest_profile():
    """Create or update an interest profile.

    Expects JSON payload with profile data including all UI fields.

    Returns:
        JSON with success/error status.
    """
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    try:
        if not _profile_service:
            return (
                jsonify(
                    {"status": "error", "message": "ProfileService not initialized"}
                ),
                500,
            )

        profile_data = request.get_json()

        # Validate required fields
        if "name" not in profile_data or not profile_data["name"].strip():
            return (
                jsonify({"status": "error", "message": "Profile name is required"}),
                400,
            )

        # Generate ID if not provided
        if "id" not in profile_data:
            existing_profiles = _profile_service.load_profiles()
            profile_data["id"] = _profile_service._generate_next_id(
                INTEREST_PROFILE_ID_PREFIX, existing_profiles
            )

        # Validate ID range if provided
        profile_id = profile_data.get("id")
        if not (3000 <= profile_id < 4000):
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Invalid interest profile ID: {profile_id}. Must be 3000-3999.",
                    }
                ),
                400,
            )

        # Ensure all expected fields have defaults
        profile_data.setdefault("description", "")
        profile_data.setdefault("enabled", True)
        profile_data.setdefault("positive_samples", [])
        profile_data.setdefault("negative_samples", [])
        profile_data.setdefault("threshold", 0.42)
        profile_data.setdefault("positive_weight", 1.0)
        profile_data.setdefault("negative_weight", 0.15)
        profile_data.setdefault("vip_senders", [])
        profile_data.setdefault("excluded_users", [])
        profile_data.setdefault("channels", [])
        profile_data.setdefault("users", [])
        profile_data.setdefault("tags", [])

        success = _profile_service.upsert_profile(profile_data)

        if success:
            return jsonify(
                {
                    "status": "ok",
                    "message": f"Interest profile '{profile_data['name']}' saved successfully",
                    "profile_id": profile_data["id"],
                }
            )
        else:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Failed to save interest profile to Sentinel",
                    }
                ),
                500,
            )

    except Exception as exc:
        logger.error(f"Failed to upsert interest profile: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@profiles_bp.delete("/interest/<int:profile_id>")
def delete_interest_profile(profile_id: int):
    """Delete an interest profile.

    Args:
        profile_id: Interest profile ID to delete (3000-3999)

    Returns:
        JSON with success/error status.
    """
    try:
        if not _profile_service:
            return (
                jsonify(
                    {"status": "error", "message": "ProfileService not initialized"}
                ),
                500,
            )

        # Validate ID range
        if not (3000 <= profile_id < 4000):
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Invalid interest profile ID: {profile_id}. Must be 3000-3999.",
                    }
                ),
                400,
            )

        success = _profile_service.delete_profile(str(profile_id))

        if success:
            return jsonify(
                {
                    "status": "ok",
                    "message": f"Interest profile {profile_id} deleted successfully",
                }
            )
        else:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Failed to delete interest profile {profile_id}",
                    }
                ),
                500,
            )

    except Exception as exc:
        logger.error(
            f"Failed to delete interest profile {profile_id}: {exc}", exc_info=True
        )
        return jsonify({"status": "error", "message": str(exc)}), 500


@profiles_bp.post("/interest/<int:profile_id>/toggle")
def toggle_interest_profile(profile_id: int):
    """Toggle interest profile enabled/disabled state.

    Args:
        profile_id: Interest profile ID to toggle (3000-3999)

    Returns:
        JSON with new enabled state.
    """
    try:
        if not _profile_service:
            return (
                jsonify(
                    {"status": "error", "message": "ProfileService not initialized"}
                ),
                500,
            )

        # Validate ID range
        if not (3000 <= profile_id < 4000):
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Invalid interest profile ID: {profile_id}. Must be 3000-3999.",
                    }
                ),
                400,
            )

        success = _profile_service.toggle_interest_profile(profile_id)

        if success:
            # Get updated profile to return new state
            profile = _profile_service.get_profile(str(profile_id))
            if profile:
                return jsonify(
                    {
                        "status": "ok",
                        "message": f"Interest profile {profile_id} toggled",
                        "enabled": profile.get("enabled", False),
                    }
                )
            else:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Interest profile {profile_id} not found after toggle",
                        }
                    ),
                    404,
                )
        else:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Failed to toggle interest profile {profile_id}",
                    }
                ),
                500,
            )

    except Exception as exc:
        logger.error(
            f"Failed to toggle interest profile {profile_id}: {exc}", exc_info=True
        )
        return jsonify({"status": "error", "message": str(exc)}), 500
