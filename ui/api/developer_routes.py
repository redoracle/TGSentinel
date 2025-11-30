"""
Developer & Webhooks API Routes Blueprint

Handles developer settings and webhook management endpoints.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Generator

import yaml
from cryptography.fernet import Fernet, InvalidToken
from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger(__name__)

# Create blueprint
developer_bp = Blueprint("developer", __name__, url_prefix="/api")

# Rate limiter (will be initialized in init_blueprint)
limiter = None

# Redis client for SSE pub/sub (injected during init)
redis_client = None

# Encryption key for webhook secrets (from environment)
_WEBHOOK_SECRET_KEY_ENV = os.environ.get("WEBHOOK_SECRET_KEY")
WEBHOOK_SECRET_KEY: bytes | None = None
WEBHOOKS_ENABLED = False

if _WEBHOOK_SECRET_KEY_ENV:
    WEBHOOK_SECRET_KEY = _WEBHOOK_SECRET_KEY_ENV.encode()
    WEBHOOKS_ENABLED = True
    logger.info("Webhook encryption key configured, webhooks feature enabled")
else:
    # Webhooks disabled without encryption key
    logger.warning(
        "WEBHOOK_SECRET_KEY not set - webhook management disabled. "
        "Set WEBHOOK_SECRET_KEY to enable webhook features."
    )

# URL validation pattern
URL_PATTERN = re.compile(
    r"^https?://"  # http:// or https://
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"  # domain...
    r"localhost|"  # localhost...
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # ...or ip
    r"(?::\d+)?"  # optional port
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)

# Dependencies (injected during registration)
config = None


def encrypt_secret(secret: str) -> str:
    """Encrypt a webhook secret using Fernet symmetric encryption."""
    if WEBHOOK_SECRET_KEY is None:
        raise ValueError("Webhook encryption not configured")
    cipher = Fernet(
        WEBHOOK_SECRET_KEY.encode()
        if isinstance(WEBHOOK_SECRET_KEY, str)
        else WEBHOOK_SECRET_KEY
    )
    return cipher.encrypt(secret.encode()).decode()


def decrypt_secret(encrypted: str) -> str:
    """Decrypt a webhook secret using Fernet symmetric encryption."""
    if WEBHOOK_SECRET_KEY is None:
        raise ValueError("Webhook encryption not configured")
    try:
        cipher = Fernet(
            WEBHOOK_SECRET_KEY.encode()
            if isinstance(WEBHOOK_SECRET_KEY, str)
            else WEBHOOK_SECRET_KEY
        )
        return cipher.decrypt(encrypted.encode()).decode()
    except InvalidToken:
        logger.error("Failed to decrypt webhook secret: invalid token")
        raise ValueError("Invalid encrypted secret")


def _publish_integration_event(event_type: str, data: dict) -> None:
    """Publish integration event to Redis for SSE subscribers."""
    if redis_client:
        try:
            event_data = {"type": event_type, **data}
            redis_client.publish(
                "tgsentinel:integration_events",
                json.dumps(event_data, ensure_ascii=False),
            )
        except Exception as exc:
            logger.error(f"Failed to publish integration event: {exc}")


def _path_factory() -> Callable[[str], Path]:
    """Return the active Path factory, honoring ui.app patches in tests."""

    app_module = sys.modules.get("ui.app") or sys.modules.get("app")
    if app_module and hasattr(app_module, "Path"):
        return getattr(app_module, "Path")
    return Path


def _resolve_config_path(filename: str) -> Any:
    path_obj = _path_factory()(f"config/{filename}")
    if hasattr(path_obj, "exists"):
        return path_obj  # honors MagicMock patching in tests
    return Path(path_obj)


def _sentinel_api_base() -> str:
    """Normalize SENTINEL_API_BASE_URL for proxying to sentinel container."""
    base = os.environ.get("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")
    return base.rstrip("/")


def _ensure_parent_dir(path_obj: Any) -> None:
    parent = getattr(path_obj, "parent", None)
    if parent and hasattr(parent, "mkdir"):
        parent.mkdir(parents=True, exist_ok=True)


def init_blueprint(
    config_obj: Any,
    ensure_init_decorator: Callable,
    redis_client_obj: Any = None,
    limiter_obj: Any = None,
) -> None:
    """Initialize blueprint with dependencies."""
    global config, redis_client, limiter
    config = config_obj
    redis_client = redis_client_obj
    limiter = limiter_obj


@developer_bp.get("/webhooks")
def api_webhooks_list():
    """List all configured webhooks."""
    if not WEBHOOKS_ENABLED:
        return jsonify(
            {
                "webhooks": [],
                "enabled": False,
                "message": "Webhooks disabled. Set WEBHOOK_SECRET_KEY environment variable to enable.",
            }
        )

    try:
        import requests

        sentinel_base = _sentinel_api_base()
        resp = requests.get(f"{sentinel_base}/webhooks", timeout=10)
        return jsonify(resp.json()), resp.status_code

    except Exception as exc:
        logger.error(f"Failed to list webhooks: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@developer_bp.get("/events/integration")
def api_integration_events():
    """Server-Sent Events stream for real-time integration console."""

    def event_stream() -> Generator[str, None, None]:
        """Generate SSE events from Redis pub/sub with keepalive."""
        if not redis_client:
            yield 'data: {"type": "error", "message": "Redis not available"}\n\n'
            return

        pubsub = None
        try:
            pubsub = redis_client.pubsub()
            pubsub.subscribe("tgsentinel:integration_events")

            # Send initial connection event
            yield 'data: {"type": "connected", "timestamp": "' + str(
                time.time()
            ) + '"}\n\n'

            # Use get_message with timeout instead of blocking listen()
            last_keepalive = time.time()
            keepalive_interval = 15  # seconds

            while True:
                message = pubsub.get_message(timeout=1.0)

                if message and message["type"] == "message":
                    # Got a real message, send it
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    yield f"data: {data}\n\n"
                    last_keepalive = time.time()

                # Send keepalive heartbeat to prevent connection timeout
                elif time.time() - last_keepalive > keepalive_interval:
                    yield f'data: {{"type": "heartbeat", "timestamp": "{time.time()}"}}\n\n'
                    last_keepalive = time.time()

        except GeneratorExit:
            # Client disconnected
            if pubsub:
                try:
                    pubsub.unsubscribe()
                    pubsub.close()
                except Exception:
                    pass
        except Exception as exc:
            logger.error(f"SSE stream error: {exc}")
            yield f'data: {{"type": "error", "message": "Stream error: {str(exc)}"}}\n\n'
            if pubsub:
                try:
                    pubsub.unsubscribe()
                    pubsub.close()
                except Exception:
                    pass

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@developer_bp.post("/webhooks")
def api_webhooks_create():
    """Create a new webhook configuration."""
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

    service = payload.get("service", "").strip()
    url = payload.get("url", "").strip()
    secret = payload.get("secret", "").strip()

    if not service or not url:
        return (
            jsonify({"status": "error", "message": "service and url are required"}),
            400,
        )

    # Validate URL format
    if not URL_PATTERN.match(url):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Invalid URL format. URL must start with http:// or https://",
                }
            ),
            400,
        )

    try:
        import requests

        sentinel_base = _sentinel_api_base()
        post_payload = {"service": service, "url": url, "enabled": True}
        if secret:
            post_payload["secret"] = encrypt_secret(secret)

        resp = requests.post(f"{sentinel_base}/webhooks", json=post_payload, timeout=10)
        if resp.status_code == 201:
            logger.info(f"Created webhook: {service}")
            _publish_integration_event(
                "webhook_created", {"service": service, "url": url}
            )
        return jsonify(resp.json()), resp.status_code

    except Exception as exc:
        logger.error(f"Failed to create webhook: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@developer_bp.post("/webhooks/test-all")
def api_webhooks_test_all():
    """Send test payload to all configured webhooks."""
    # Apply rate limiting if limiter is configured
    if limiter:
        try:
            limiter.check()
        except Exception:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Rate limit exceeded. Please wait before testing again.",
                    }
                ),
                429,
            )

    try:
        webhooks_path = _resolve_config_path("webhooks.yml")
        if not webhooks_path.exists():
            return jsonify(
                {"status": "ok", "results": [], "message": "No webhooks configured"}
            )

        with open(webhooks_path, "r") as f:
            data = yaml.safe_load(f) or {}

        webhooks = data.get("webhooks", [])

        dry_run = request.args.get("dry_run", "false").lower() == "true"

        if not webhooks:
            return jsonify(
                {
                    "status": "ok",
                    "results": [],
                    "message": (
                        "No webhooks configured"
                        if not dry_run
                        else "Dry-run: no webhooks configured"
                    ),
                }
            )

        # Import requests here to avoid dependency issues
        try:
            import requests
        except ImportError:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "requests library not available - cannot test webhook delivery",
                    }
                ),
                500,
            )

        results = []

        for webhook in webhooks:
            service = webhook.get("service", "unknown")
            url = webhook.get("url")
            secret = webhook.get("secret", "")
            enabled = webhook.get("enabled", True)

            if not enabled:
                results.append(
                    {
                        "service": service,
                        "status": "skipped",
                        "message": "Webhook is disabled",
                    }
                )
                continue

            if dry_run:
                results.append(
                    {
                        "service": service,
                        "status": "ok",
                        "status_code": None,
                        "response_time_ms": None,
                        "message": "Dry-run: no request sent",
                        "payload": {
                            "event": "webhook_test",
                            "service": service,
                            "timestamp": "2025-01-01T00:00:00Z",
                            "message": "This is a test alert from TG Sentinel developer panel",
                            "severity": "info",
                            "source": "developer_panel",
                        },
                    }
                )
                continue

            # Decrypt secret if present and not masked
            decrypted_secret = ""
            if secret and not str(secret).startswith("•"):
                try:
                    decrypted_secret = decrypt_secret(secret)
                except Exception as exc:
                    logger.warning(
                        f"Failed to decrypt webhook secret for {service}: {exc}. Proceeding without signature."
                    )
                    decrypted_secret = ""

            # Prepare test payload
            test_payload = {
                "event": "sample_alert",
                "service": service,
                "timestamp": "2025-01-01T00:00:00Z",
                "message": "Sample alert from TG Sentinel integration console",
                "severity": "info",
                "source": "developer_panel",
                "alert_type": "test",
            }

            headers = {"Content-Type": "application/json"}

            # Add signature if secret is configured
            if decrypted_secret:
                import hmac
                import json

                body = json.dumps(test_payload)
                signature = hmac.new(
                    decrypted_secret.encode(), body.encode(), hashlib.sha256
                ).hexdigest()
                headers["X-Webhook-Signature"] = f"sha256={signature}"

            try:
                response = requests.post(
                    url, json=test_payload, headers=headers, timeout=10
                )

                _publish_integration_event(
                    "webhook_test",
                    {
                        "service": service,
                        "status": "success",
                        "status_code": response.status_code,
                    },
                )

                results.append(
                    {
                        "service": service,
                        "status": "ok",
                        "status_code": response.status_code,
                        "response_time_ms": int(
                            response.elapsed.total_seconds() * 1000
                        ),
                        "message": f"HTTP {response.status_code}",
                    }
                )

            except requests.exceptions.Timeout:
                results.append(
                    {
                        "service": service,
                        "status": "error",
                        "message": "Request timed out after 10 seconds",
                    }
                )
            except requests.exceptions.RequestException as req_err:
                results.append(
                    {
                        "service": service,
                        "status": "error",
                        "message": f"Delivery failed: {str(req_err)}",
                    }
                )

        logger.info(f"Tested {len(webhooks)} webhooks")
        return jsonify({"status": "ok", "results": results})

    except Exception as exc:
        logger.error(f"Failed to test webhooks: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@developer_bp.post("/webhooks/<service_name>/test")
def api_webhooks_test(service_name: str):
    """Send a test payload to the specified webhook."""
    try:
        import requests

        # Import requests here to avoid dependency issues
        sentinel_base = _sentinel_api_base()
        # Fetch webhook details from sentinel to avoid local file dependency
        list_resp = requests.get(f"{sentinel_base}/webhooks", timeout=10)
        if list_resp.status_code != 200:
            return jsonify(list_resp.json()), list_resp.status_code
        webhooks = list_resp.json().get("webhooks", [])
        webhook = next(
            (wh for wh in webhooks if wh.get("service") == service_name), None
        )
        if not webhook:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Webhook '{service_name}' not found",
                    }
                ),
                404,
            )

        # Prepare test payload
        test_payload = {
            "event": "webhook_test",
            "service": service_name,
            "timestamp": "2025-01-01T00:00:00Z",
            "message": "This is a test alert from TG Sentinel developer panel",
            "severity": "info",
            "source": "developer_panel",
        }

        dry_run = request.args.get("dry_run", "false").lower() == "true"
        if dry_run:
            return jsonify(
                {
                    "status": "ok",
                    "service": service_name,
                    "status_code": None,
                    "response_time_ms": None,
                    "message": "Dry-run: no request sent",
                    "payload": test_payload,
                }
            )

        # Send HTTP POST to webhook URL
        url = webhook.get("url")
        secret = webhook.get("secret", "")

        headers = {"Content-Type": "application/json"}

        # Add signature if secret is configured and not masked
        if secret and not str(secret).startswith("•"):
            import hmac

            # Decrypt the stored encrypted secret before computing HMAC
            try:
                decrypted_secret = decrypt_secret(secret)
            except (ValueError, Exception) as exc:
                logger.warning(
                    f"Failed to decrypt webhook secret for {service_name}: {exc}. Proceeding without signature."
                )
                decrypted_secret = ""

            if decrypted_secret:
                body = json.dumps(test_payload)
                signature = hmac.new(
                    decrypted_secret.encode(), body.encode(), hashlib.sha256
                ).hexdigest()
                headers["X-Webhook-Signature"] = f"sha256={signature}"

        try:
            response = requests.post(
                url, json=test_payload, headers=headers, timeout=10
            )

            logger.info(
                f"Webhook test sent to {service_name}: HTTP {response.status_code}"
            )

            return jsonify(
                {
                    "status": "ok",
                    "service": service_name,
                    "status_code": response.status_code,
                    "response_time_ms": int(response.elapsed.total_seconds() * 1000),
                    "message": f"Webhook delivered successfully (HTTP {response.status_code})",
                }
            )

        except requests.exceptions.Timeout:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Webhook request timed out after 10 seconds",
                    }
                ),
                504,
            )
        except requests.exceptions.RequestException as req_err:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Failed to deliver webhook: {str(req_err)}",
                    }
                ),
                502,
            )

    except Exception as exc:
        logger.error(f"Failed to test webhook: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@developer_bp.delete("/webhooks/<service_name>")
def api_webhooks_delete(service_name: str):
    """Delete a webhook by service name."""
    try:
        import requests

        sentinel_base = _sentinel_api_base()
        resp = requests.delete(f"{sentinel_base}/webhooks/{service_name}", timeout=10)
        if resp.status_code == 200:
            logger.info(f"Deleted webhook: {service_name}")
            _publish_integration_event("webhook_deleted", {"service": service_name})
        return jsonify(resp.json()), resp.status_code

    except Exception as exc:
        logger.error(f"Failed to delete webhook: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@developer_bp.patch("/webhooks/<service_name>")
def api_webhooks_update(service_name: str):
    """Update an existing webhook configuration."""
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

    try:
        import requests

        # Basic validation for URL if present
        if "url" in payload:
            url = payload["url"].strip()
            if not URL_PATTERN.match(url):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Invalid URL format. URL must start with http:// or https://",
                        }
                    ),
                    400,
                )

        sentinel_base = _sentinel_api_base()
        patch_payload = {}
        if "url" in payload:
            patch_payload["url"] = payload["url"]
        if "secret" in payload:
            patch_payload["secret"] = (
                encrypt_secret(payload["secret"]) if payload["secret"] else ""
            )
        if "enabled" in payload:
            patch_payload["enabled"] = bool(payload["enabled"])

        resp = requests.patch(
            f"{sentinel_base}/webhooks/{service_name}",
            json=patch_payload,
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info(f"Updated webhook: {service_name}")
            _publish_integration_event("webhook_updated", {"service": service_name})
        return jsonify(resp.json()), resp.status_code

    except Exception as exc:
        logger.error(f"Failed to update webhook: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@developer_bp.get("/webhooks/history")
def api_webhooks_history():
    """Get recent webhook delivery history from Sentinel.

    Query Parameters:
        limit: Maximum number of records to return (default: 10, max: 100)

    Returns:
        JSON with recent webhook deliveries including status, timing, and errors
    """
    # Import requests here to avoid dependency issues
    try:
        import requests
    except ImportError:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "requests library not available - cannot fetch webhook history",
                }
            ),
            500,
        )

    try:
        # Get limit parameter
        limit = request.args.get("limit", default=10, type=int)
        limit = max(1, min(limit, 100))  # Clamp between 1 and 100

        # Proxy request to Sentinel API
        sentinel_url = os.environ.get("SENTINEL_API_BASE_URL", "http://sentinel:8080")
        # Normalize to avoid double /api when env already includes it
        sentinel_url = sentinel_url.rstrip("/")
        sentinel_url = (
            sentinel_url[:-4] if sentinel_url.endswith("/api") else sentinel_url
        )
        response = requests.get(
            f"{sentinel_url}/api/webhooks/history", params={"limit": limit}, timeout=10
        )

        if response.status_code != 200:
            logger.error(
                f"Sentinel API returned {response.status_code}: {response.text}"
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Sentinel API error: {response.status_code}",
                    }
                ),
                response.status_code,
            )

        data = response.json()
        return jsonify(data), 200

    except requests.exceptions.RequestException as req_exc:
        logger.error(f"Failed to connect to Sentinel API: {req_exc}")
        return (
            jsonify(
                {"status": "error", "message": "Failed to connect to Sentinel service"}
            ),
            503,
        )

    except Exception as exc:
        logger.error(f"Failed to fetch webhook history: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@developer_bp.get("/developer/settings")
def api_developer_settings_get():
    """Get current developer settings."""
    try:
        settings_path = _resolve_config_path("developer.yml")

        if not settings_path.exists():
            return jsonify({"prometheus_port": 9090, "metrics_enabled": True})

        with open(settings_path, "r") as f:
            data = yaml.safe_load(f) or {}

        return jsonify(
            {
                "prometheus_port": data.get("prometheus_port", 9090),
                "metrics_enabled": data.get("metrics_enabled", True),
            }
        )

    except Exception as exc:
        logger.error(f"Failed to load developer settings: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@developer_bp.post("/developer/settings")
def api_developer_settings():
    """Save developer integration settings."""
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

    try:
        settings_path = _resolve_config_path("developer.yml")
        _ensure_parent_dir(settings_path)

        # Load existing settings
        if settings_path.exists():
            with open(settings_path, "r") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        # Update settings
        if "prometheus_port" in payload:
            port = payload["prometheus_port"]
            if not isinstance(port, int) or port < 1 or port > 65535:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "prometheus_port must be between 1 and 65535",
                        }
                    ),
                    400,
                )
            data["prometheus_port"] = port

        if "api_key" in payload:
            # Store hashed version of API key for security
            api_key = payload["api_key"]
            key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            data["api_key_hash"] = key_hash

        if "metrics_enabled" in payload:
            data["metrics_enabled"] = bool(payload["metrics_enabled"])

        # Save atomically
        temp_dir = (
            settings_path.parent
            if isinstance(settings_path, Path)
            else getattr(settings_path, "parent", None)
        )

        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=temp_dir or None,
            suffix=".tmp",
        ) as tmp_file:
            yaml.dump(data, tmp_file, default_flow_style=False, sort_keys=False)
            tmp_path = tmp_file.name

        shutil.move(tmp_path, settings_path)

        logger.info("Saved developer settings")
        _publish_integration_event(
            "settings_updated", {"metrics_enabled": data.get("metrics_enabled", True)}
        )
        return jsonify({"status": "ok"})

    except Exception as exc:
        logger.error(f"Failed to save developer settings: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


def is_api_key_revoked(api_key: str) -> bool:
    """Check if an API key has been revoked.

    Args:
        api_key: The API key to check

    Returns:
        True if the key is revoked, False otherwise
    """
    if not redis_client:
        return False

    try:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        revoked_key = f"tgsentinel:revoked_api_key:{key_hash}"
        # Check if the per-hash revocation key exists
        return bool(redis_client.exists(revoked_key))
    except Exception as exc:
        logger.error(f"Failed to check API key revocation status: {exc}")
        return False


@developer_bp.post("/api-keys/revoke")
def api_key_revoke():
    """Revoke an API key by creating a per-hash Redis key with 30-day TTL."""
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

    api_key = payload.get("api_key", "").strip()
    if not api_key:
        return (
            jsonify({"status": "error", "message": "api_key is required"}),
            400,
        )

    try:
        # Hash the key
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        if redis_client:
            # Create individual Redis key for this revoked hash with 30-day TTL
            revoked_key = f"tgsentinel:revoked_api_key:{key_hash}"
            ttl_seconds = 30 * 24 * 3600  # 30 days

            # Set the key with value "1" and TTL
            redis_client.setex(revoked_key, ttl_seconds, "1")

            # Optionally maintain a set for listing (without relying on its TTL)
            # This allows querying all revoked keys, but each key expires individually
            redis_client.sadd("tgsentinel:revoked_api_keys", key_hash)

            logger.info(
                f"Revoked API key with hash: {key_hash[:8]}... (expires in 30 days)"
            )
            _publish_integration_event("api_key_revoked", {"key_hash": key_hash[:8]})
            return jsonify({"status": "ok", "message": "API key revoked successfully"})
        else:
            return jsonify({"status": "error", "message": "Redis not available"}), 500

    except Exception as exc:
        logger.error(f"Failed to revoke API key: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500
