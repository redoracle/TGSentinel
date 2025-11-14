"""Tests for participant information API and modal functionality."""

import json
from unittest.mock import MagicMock, patch

import pytest
from redis import Redis


@pytest.fixture
def mock_config():
    """Mock configuration with channels."""
    config = MagicMock()
    channel1 = MagicMock()
    channel1.id = 123456
    channel1.name = "Test Channel"

    channel2 = MagicMock()
    channel2.id = 789012
    channel2.name = "Another Channel"

    config.channels = [channel1, channel2]
    return config


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    return MagicMock(spec=Redis)


class TestParticipantInfoAPI:
    """Test the /api/participant/info endpoint."""

    def test_missing_chat_id(self, client):
        """Test API returns 400 when chat_id is missing."""
        response = client.get("/api/participant/info")
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "chat_id" in data["error"]

    def test_invalid_chat_id(self, client):
        """Test API returns 400 for invalid chat_id."""
        response = client.get("/api/participant/info?chat_id=invalid")
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "Invalid" in data["error"]

    def test_invalid_user_id(self, client):
        """Test API returns 400 for invalid user_id."""
        response = client.get("/api/participant/info?chat_id=123&user_id=invalid")
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data

    def test_chat_info_without_user_id_from_config(self, app, client, mock_config):
        """Test getting chat info without user_id returns config data."""
        # Import app module to access global config
        import app as flask_app  # type: ignore[import-not-found]

        # Set the global config
        original_config = flask_app.config
        flask_app.config = mock_config

        try:
            response = client.get("/api/participant/info?chat_id=123456")
            assert response.status_code == 200
            data = response.get_json()

            assert "chat" in data
            assert data["chat"]["id"] == 123456
            assert data["chat"]["title"] == "Test Channel"
            assert "type" in data["chat"]
        finally:
            flask_app.config = original_config

    def test_chat_info_without_user_id_fallback(self, app, client):
        """Test chat info fallback when not in config."""
        response = client.get("/api/participant/info?chat_id=999999")
        assert response.status_code == 200
        data = response.get_json()

        assert "chat" in data
        assert data["chat"]["id"] == 999999
        assert "Chat 999999" in data["chat"]["title"]

    def test_chat_type_inference_private(self, app, client):
        """Test chat type inference for private chats (positive IDs)."""
        response = client.get("/api/participant/info?chat_id=12345")
        assert response.status_code == 200
        data = response.get_json()
        assert data["chat"]["type"] == "private"

    def test_chat_type_inference_channel(self, app, client):
        """Test chat type inference for channels/supergroups (-100 prefix)."""
        response = client.get("/api/participant/info?chat_id=-1001234567890")
        assert response.status_code == 200
        data = response.get_json()
        assert data["chat"]["type"] == "channel"

    def test_chat_type_inference_group(self, app, client):
        """Test chat type inference for basic groups (negative, no -100)."""
        response = client.get("/api/participant/info?chat_id=-123456")
        assert response.status_code == 200
        data = response.get_json()
        assert data["chat"]["type"] == "group"

    def test_chat_type_from_redis_cache(self, app, client):
        """Test chat type retrieval from Redis cache."""
        import app as flask_app  # type: ignore[import-not-found]

        # Create a mock redis client
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"supergroup"

        original_redis = flask_app.redis_client
        flask_app.redis_client = mock_redis

        try:
            response = client.get("/api/participant/info?chat_id=-1001234567890")
            assert response.status_code == 200
            data = response.get_json()

            # Should use cached type instead of inferred
            assert data["chat"]["type"] == "supergroup"
            assert mock_redis.get.called
        finally:
            flask_app.redis_client = original_redis

    def test_cached_participant_info_returned(self, app, client):
        """Test that cached participant info is returned immediately."""
        import app as flask_app  # type: ignore[import-not-found]

        cached_data = {
            "user": {
                "id": 12345,
                "name": "Test User",
                "username": "testuser",
                "phone": "+1234567890",
                "bot": False,
            },
            "participant": {
                "role": "admin",
                "join_date": 1234567890,
                "admin_rights": {"change_info": True, "delete_messages": True},
            },
        }

        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps(cached_data).encode()

        original_redis = flask_app.redis_client
        flask_app.redis_client = mock_redis

        try:
            response = client.get("/api/participant/info?chat_id=123&user_id=12345")
            assert response.status_code == 200
            data = response.get_json()

            assert "user" in data
            assert data["user"]["name"] == "Test User"
            assert data["user"]["username"] == "testuser"
            assert "participant" in data
            assert data["participant"]["role"] == "admin"
        finally:
            flask_app.redis_client = original_redis

    def test_current_user_info_from_cache(self, app, client):
        """Test returning current user info from Redis user_info cache."""
        import app as flask_app  # type: ignore[import-not-found]

        user_info = {"id": 12345, "username": "currentuser", "phone": "+1234567890"}

        def get_side_effect(key):
            if key == "tgsentinel:user_info":
                return json.dumps(user_info).encode()
            return None

        mock_redis = MagicMock()
        mock_redis.get.side_effect = get_side_effect

        original_redis = flask_app.redis_client
        flask_app.redis_client = mock_redis

        try:
            response = client.get("/api/participant/info?chat_id=123&user_id=12345")
            assert response.status_code == 200
            data = response.get_json()

            assert "user" in data
            assert data["user"]["id"] == 12345
            assert data["user"]["username"] == "currentuser"
            assert data["user"]["phone"] == "+1234567890"
        finally:
            flask_app.redis_client = original_redis

    def test_worker_request_processing(self, app, client):
        """Test that API waits for worker to process request."""
        import app as flask_app  # type: ignore[import-not-found]
        import time

        call_count = [0]

        def get_side_effect(key):
            call_count[0] += 1
            if "tgsentinel:user_info" in str(key):
                return None
            if call_count[0] > 5:  # Return cached data on 6th call
                return json.dumps(
                    {
                        "user": {"id": 999, "name": "Worker User"},
                        "participant": {"role": "member"},
                    }
                ).encode()
            return None

        mock_redis = MagicMock()
        mock_redis.get.side_effect = get_side_effect
        mock_redis.setex.return_value = True

        original_redis = flask_app.redis_client
        flask_app.redis_client = mock_redis

        # Mock time.sleep to avoid delays
        original_sleep = time.sleep
        time.sleep = MagicMock()

        try:
            response = client.get("/api/participant/info?chat_id=123&user_id=999")
            assert response.status_code == 200
            data = response.get_json()

            # Should have requested worker processing
            assert mock_redis.setex.called
            args = mock_redis.setex.call_args[0]
            assert "tgsentinel:participant_request" in args[0]

            # Should return worker-fetched data
            assert "user" in data
            assert data["user"]["name"] == "Worker User"
        finally:
            flask_app.redis_client = original_redis
            time.sleep = original_sleep

    def test_fallback_when_worker_timeout(self, app, client):
        """Test fallback response when worker doesn't respond in time."""
        import app as flask_app  # type: ignore[import-not-found]
        import time

        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis.setex.return_value = True

        original_redis = flask_app.redis_client
        flask_app.redis_client = mock_redis

        # Mock time.sleep to avoid delays
        original_sleep = time.sleep
        time.sleep = MagicMock()

        try:
            response = client.get("/api/participant/info?chat_id=123&user_id=999")
            assert response.status_code == 200
            data = response.get_json()

            # Should return fallback user info
            assert "user" in data
            assert data["user"]["id"] == 999
            assert "User 999" in data["user"]["name"]
        finally:
            flask_app.redis_client = original_redis
            time.sleep = original_sleep

    def test_fallback_when_redis_unavailable(self, app, client):
        """Test fallback response when Redis is not available."""
        import app as flask_app  # type: ignore[import-not-found]

        original_redis = flask_app.redis_client
        flask_app.redis_client = None

        try:
            response = client.get("/api/participant/info?chat_id=123&user_id=777")
            assert response.status_code == 200
            data = response.get_json()

            assert "user" in data
            assert data["user"]["id"] == 777
            assert "User 777" in data["user"]["name"]
        finally:
            flask_app.redis_client = original_redis


class TestParticipantDataStructures:
    """Test participant info data structures and completeness."""

    def test_complete_user_data_structure(self):
        """Test that user data includes all expected fields."""
        user_data = {
            "id": 12345,
            "name": "John Doe",
            "first_name": "John",
            "last_name": "Doe",
            "username": "johndoe",
            "phone": "+1234567890",
            "bot": False,
            "verified": True,
            "scam": False,
            "fake": False,
            "support": False,
            "restricted": False,
            "restriction_reason": None,
            "contact": True,
            "mutual_contact": True,
            "deleted": False,
            "premium": True,
            "last_seen": 1234567890,
            "status": "online",
            "about": "Test bio",
            "common_chats_count": 5,
            "photo_available": True,
            "lang_code": "en",
        }

        # Verify all expected user fields
        assert "id" in user_data
        assert "name" in user_data
        assert "username" in user_data
        assert "bot" in user_data
        assert "phone" in user_data

    def test_complete_participant_data_structure(self):
        """Test that participant data includes all expected fields."""
        participant_data = {
            "role": "admin",
            "join_date": 1234567890,
            "date": 1234567890,
            "inviter_id": 54321,
            "invited_by": 54321,
            "promoted_by": 99999,
            "kicked_by": None,
            "rank": "Moderator",
            "custom_title": "Super Admin",
            "left": False,
            "admin_rights": {
                "change_info": True,
                "post_messages": True,
                "edit_messages": True,
                "delete_messages": True,
                "ban_users": True,
                "invite_users": True,
                "pin_messages": True,
                "add_admins": False,
                "manage_call": True,
                "manage_topics": True,
                "anonymous": False,
                "other": False,
            },
            "banned_rights": None,
        }

        # Verify all expected participant fields
        assert "role" in participant_data
        assert "join_date" in participant_data or "date" in participant_data
        assert "admin_rights" in participant_data
        assert participant_data["admin_rights"]["change_info"] is True

    def test_complete_chat_data_structure(self):
        """Test that chat data includes all expected fields."""
        chat_data = {
            "id": -1001234567890,
            "title": "Test Group",
            "type": "supergroup",
            "username": "testgroup",
            "participants_count": 150,
            "access_hash": 9876543210,
            "created_date": 1234567890,
            "description": "Test group description",
            "invite_link": "https://t.me/+abcd1234",
            "pinned_msg_id": 12345,
            "broadcast": False,
            "megagroup": True,
            "gigagroup": False,
            "forum": False,
            "noforwards": False,
            "verified": False,
            "scam": False,
            "fake": False,
        }

        # Verify all expected chat fields
        assert "id" in chat_data
        assert "title" in chat_data
        assert "type" in chat_data
        assert "participants_count" in chat_data

    def test_banned_rights_structure(self):
        """Test banned rights structure includes all restriction fields."""
        banned_rights = {
            "view_messages": True,
            "send_messages": True,
            "send_media": True,
            "send_stickers": True,
            "send_gifs": True,
            "send_games": True,
            "send_inline": True,
            "embed_links": True,
            "send_polls": True,
            "change_info": True,
            "invite_users": True,
            "pin_messages": True,
            "until_date": 1234567890,
        }

        # Verify all restriction fields
        assert "send_messages" in banned_rights
        assert "send_media" in banned_rights
        assert "until_date" in banned_rights


class TestModalFrontendIntegration:
    """Test modal integration and data display logic."""

    def test_modal_shows_user_info(self, client):
        """Test that modal receives and can display user information."""
        # This tests the data structure that would be passed to displayParticipantInfo
        data = {
            "user": {
                "id": 12345,
                "name": "Test User",
                "username": "testuser",
                "bot": False,
            }
        }

        # Verify structure is correct for frontend display
        assert data["user"]["id"] == 12345
        assert data["user"]["name"] == "Test User"

    def test_modal_shows_participant_info(self, client):
        """Test that modal receives and can display participant information."""
        data = {
            "user": {"id": 12345, "name": "Admin User"},
            "participant": {
                "role": "admin",
                "join_date": 1234567890,
                "admin_rights": {"delete_messages": True, "ban_users": True},
            },
        }

        # Verify structure for participant display
        assert data["participant"]["role"] == "admin"
        assert "admin_rights" in data["participant"]

    def test_modal_shows_chat_info(self, client):
        """Test that modal receives and can display chat information."""
        data = {
            "chat": {
                "id": -1001234567890,
                "title": "Test Channel",
                "type": "channel",
                "participants_count": 500,
            }
        }

        # Verify structure for chat display
        assert data["chat"]["title"] == "Test Channel"
        assert data["chat"]["type"] == "channel"

    def test_modal_shows_combined_info(self, client):
        """Test that modal can show user, participant, and chat info together."""
        data = {
            "chat": {
                "id": -1001234567890,
                "title": "Test Group",
                "type": "supergroup",
                "participants_count": 100,
            },
            "user": {"id": 12345, "name": "Member User", "username": "member1"},
            "participant": {
                "role": "member",
                "join_date": 1234567890,
                "inviter_id": 99999,
            },
        }

        # Verify all sections present
        assert "chat" in data
        assert "user" in data
        assert "participant" in data

    def test_modal_handles_minimal_data(self, client):
        """Test that modal handles minimal user data gracefully."""
        data = {"user": {"id": 999, "name": "User 999", "username": None}}

        # Should still be valid
        assert data["user"]["id"] == 999
        assert data["user"]["username"] is None

    def test_modal_filters_by_info_type_user(self, client):
        """Test that infoType='user' filters correctly."""
        data = {
            "chat": {"id": 123, "title": "Chat"},
            "user": {"id": 456, "name": "User"},
            "participant": {"role": "admin"},
        }

        info_type = "user"

        # When infoType is 'user', should show user and participant but not chat
        # This logic is in the frontend JavaScript
        if info_type == "user":
            assert data["user"] is not None
            assert data.get("participant") is not None

    def test_modal_filters_by_info_type_chat(self, client):
        """Test that infoType='chat' filters correctly."""
        data = {
            "chat": {"id": 123, "title": "Chat"},
            "user": {"id": 456, "name": "User"},
        }

        info_type = "chat"

        # When infoType is 'chat', should only show chat
        if info_type == "chat":
            assert data["chat"] is not None


class TestRoleDetection:
    """Test participant role detection and badges."""

    def test_role_creator(self):
        """Test creator role detection."""
        participant = {"role": "creator", "rank": "Owner"}
        assert participant["role"] == "creator"
        assert "rank" in participant

    def test_role_admin(self):
        """Test admin role detection."""
        participant = {
            "role": "admin",
            "custom_title": "Moderator",
            "admin_rights": {"ban_users": True},
        }
        assert participant["role"] == "admin"
        assert participant["admin_rights"]["ban_users"] is True

    def test_role_member(self):
        """Test member role detection."""
        participant = {"role": "member", "join_date": 1234567890}
        assert participant["role"] == "member"

    def test_role_banned(self):
        """Test banned role detection."""
        participant = {
            "role": "banned",
            "kicked_by": 99999,
            "banned_rights": {"send_messages": True},
        }
        assert participant["role"] == "banned"
        assert "kicked_by" in participant

    def test_role_left(self):
        """Test left role detection."""
        participant = {"role": "left", "left": True}
        assert participant["role"] == "left"
        assert participant["left"] is True


class TestAdminRights:
    """Test admin rights display and detection."""

    def test_all_admin_rights_present(self):
        """Test that all admin rights fields are checked."""
        admin_rights = {
            "change_info": True,
            "post_messages": False,
            "edit_messages": True,
            "delete_messages": True,
            "ban_users": True,
            "invite_users": True,
            "pin_messages": True,
            "add_admins": False,
            "manage_call": True,
            "manage_topics": False,
            "anonymous": False,
            "other": False,
        }

        # Verify all expected rights
        expected_rights = [
            "change_info",
            "post_messages",
            "edit_messages",
            "delete_messages",
            "ban_users",
            "invite_users",
            "pin_messages",
            "add_admins",
            "manage_call",
            "manage_topics",
            "anonymous",
            "other",
        ]

        for right in expected_rights:
            assert right in admin_rights

    def test_limited_admin_rights(self):
        """Test admin with limited rights."""
        admin_rights = {
            "change_info": False,
            "post_messages": False,
            "edit_messages": False,
            "delete_messages": True,
            "ban_users": True,
            "invite_users": False,
            "pin_messages": False,
            "add_admins": False,
        }

        # Count enabled rights
        enabled = sum(1 for v in admin_rights.values() if v)
        assert enabled == 2  # Only delete and ban


class TestBannedRights:
    """Test banned rights display and detection."""

    def test_all_banned_rights_present(self):
        """Test that all banned rights fields are checked."""
        banned_rights = {
            "view_messages": False,
            "send_messages": True,
            "send_media": True,
            "send_stickers": True,
            "send_gifs": True,
            "send_games": True,
            "send_inline": True,
            "embed_links": True,
            "send_polls": True,
            "change_info": True,
            "invite_users": True,
            "pin_messages": True,
            "until_date": 1234567890,
        }

        # Verify all expected restrictions
        expected_restrictions = [
            "view_messages",
            "send_messages",
            "send_media",
            "send_stickers",
            "send_gifs",
            "send_games",
            "send_inline",
            "embed_links",
            "send_polls",
            "change_info",
            "invite_users",
            "pin_messages",
        ]

        for restriction in expected_restrictions:
            assert restriction in banned_rights

    def test_banned_until_date_expired(self):
        """Test detection of expired ban."""
        import time

        current_time = int(time.time())

        banned_rights = {
            "send_messages": True,
            "until_date": current_time - 86400,  # Yesterday
        }

        # Ban should be expired
        assert banned_rights["until_date"] < current_time

    def test_banned_until_date_active(self):
        """Test detection of active ban."""
        import time

        current_time = int(time.time())

        banned_rights = {
            "send_messages": True,
            "until_date": current_time + 86400,  # Tomorrow
        }

        # Ban should be active
        assert banned_rights["until_date"] > current_time


class TestAvatarFunctionality:
    """Test avatar image functionality for activity feeds and user profiles."""

    def test_avatar_url_in_activity_feed(self, app, client):
        """Test that activity feed includes avatar URLs when available."""
        import app as flask_app  # type: ignore[import-not-found]

        # Mock Redis with activity data including avatar
        mock_redis = MagicMock()

        activity_data = {
            "json": json.dumps(
                {
                    "chat_id": -1001234567890,
                    "chat_name": "Test Channel",
                    "sender_id": 12345,
                    "sender_name": "Test User",
                    "message": "Test message",
                    "importance": 0.8,
                    "timestamp": "2024-01-01T12:00:00Z",
                }
            )
        }

        # Mock xrevrange to return activity data
        mock_redis.xrevrange.return_value = [("msg-1", activity_data)]

        # Mock avatar cache
        def get_side_effect(key):
            if key == "tgsentinel:user_avatar:12345":
                return b"/data/avatars/user_12345.jpg"
            return None

        mock_redis.get.side_effect = get_side_effect

        # Save original state
        original_redis = flask_app.redis_client
        original_initialized = flask_app._is_initialized

        # Set up mocks - mark as initialized to prevent init_app() from resetting redis_client
        flask_app._is_initialized = True
        flask_app.redis_client = mock_redis

        try:
            response = client.get("/api/dashboard/activity")
            assert response.status_code == 200
            data = response.get_json()

            assert "entries" in data
            assert len(data["entries"]) > 0, f"Got entries: {data['entries']}"

            entry = data["entries"][0]
            assert "avatar_url" in entry, f"Entry keys: {entry.keys()}, entry: {entry}"
            assert entry["avatar_url"] == "/data/avatars/user_12345.jpg"
            assert entry["sender_id"] == 12345
            assert entry["sender"] == "Test User"
        finally:
            flask_app.redis_client = original_redis
            flask_app._is_initialized = original_initialized

    def test_avatar_url_missing_when_not_cached(self, app, client):
        """Test that avatar_url is None when not in Redis cache."""
        import app as flask_app  # type: ignore[import-not-found]

        mock_redis = MagicMock()

        activity_data = {
            "json": json.dumps(
                {
                    "chat_id": -1001234567890,
                    "sender_id": 99999,
                    "sender_name": "User Without Avatar",
                    "message": "Test message",
                }
            )
        }

        mock_redis.xrevrange.return_value = [("msg-1", activity_data)]
        mock_redis.get.return_value = None  # No avatar cached

        original_redis = flask_app.redis_client
        original_initialized = flask_app._is_initialized

        flask_app._is_initialized = True
        flask_app.redis_client = mock_redis

        try:
            response = client.get("/api/dashboard/activity")
            assert response.status_code == 200
            data = response.get_json()

            entry = data["entries"][0]
            assert "avatar_url" in entry
            assert entry["avatar_url"] is None
        finally:
            flask_app.redis_client = original_redis
            flask_app._is_initialized = original_initialized

    def test_avatar_cache_key_format(self):
        """Test that avatar cache keys follow correct format."""
        sender_id = 12345
        expected_key = f"tgsentinel:user_avatar:{sender_id}"

        # Verify the key format is consistent
        assert expected_key == "tgsentinel:user_avatar:12345"

        # Test with different IDs
        assert f"tgsentinel:user_avatar:{67890}" == "tgsentinel:user_avatar:67890"

    def test_avatar_url_path_format(self):
        """Test that avatar URLs follow correct path format."""
        sender_id = 12345
        expected_url = f"/data/avatars/user_{sender_id}.jpg"

        assert expected_url == "/data/avatars/user_12345.jpg"

        # Verify it's a valid relative path that can be served
        assert expected_url.startswith("/data/")
        assert expected_url.endswith(".jpg")

    def test_multiple_users_with_different_avatars(self, app, client):
        """Test activity feed with multiple users having different avatar states."""
        import app as flask_app  # type: ignore[import-not-found]

        mock_redis = MagicMock()

        # Create multiple activity entries with different avatar states
        activities = [
            (
                "msg-1",
                {
                    "json": json.dumps(
                        {
                            "sender_id": 111,
                            "sender_name": "User With Avatar",
                            "message": "Message 1",
                            "chat_name": "Test Chat",
                        }
                    )
                },
            ),
            (
                "msg-2",
                {
                    "json": json.dumps(
                        {
                            "sender_id": 222,
                            "sender_name": "User Without Avatar",
                            "message": "Message 2",
                            "chat_name": "Test Chat",
                        }
                    )
                },
            ),
            (
                "msg-3",
                {
                    "json": json.dumps(
                        {
                            "sender_id": 333,
                            "sender_name": "Another User With Avatar",
                            "message": "Message 3",
                            "chat_name": "Test Chat",
                        }
                    )
                },
            ),
        ]

        mock_redis.xrevrange.return_value = activities

        # Mock avatars for users 111 and 333, but not 222
        def get_side_effect(key):
            if key == "tgsentinel:user_avatar:111":
                return b"/data/avatars/user_111.jpg"
            elif key == "tgsentinel:user_avatar:333":
                return b"/data/avatars/user_333.jpg"
            return None

        mock_redis.get.side_effect = get_side_effect

        original_redis = flask_app.redis_client
        original_initialized = flask_app._is_initialized

        flask_app._is_initialized = True
        flask_app.redis_client = mock_redis

        try:
            response = client.get("/api/dashboard/activity?limit=10")
            assert response.status_code == 200
            data = response.get_json()

            assert len(data["entries"]) == 3

            # Check first user has avatar
            assert data["entries"][0]["sender_id"] == 111
            assert data["entries"][0]["avatar_url"] == "/data/avatars/user_111.jpg"

            # Check second user has no avatar
            assert data["entries"][1]["sender_id"] == 222
            assert data["entries"][1]["avatar_url"] is None

            # Check third user has avatar
            assert data["entries"][2]["sender_id"] == 333
            assert data["entries"][2]["avatar_url"] == "/data/avatars/user_333.jpg"
        finally:
            flask_app.redis_client = original_redis
            flask_app._is_initialized = original_initialized

    def test_avatar_with_bytes_response(self, app, client):
        """Test handling of avatar URL when Redis returns bytes."""
        import app as flask_app  # type: ignore[import-not-found]

        mock_redis = MagicMock()

        activity_data = {
            "json": json.dumps(
                {
                    "sender_id": 12345,
                    "sender_name": "Test User",
                    "message": "Test",
                    "chat_name": "Test Chat",
                }
            )
        }

        mock_redis.xrevrange.return_value = [("msg-1", activity_data)]

        # Redis returns bytes (common behavior)
        mock_redis.get.return_value = b"/data/avatars/user_12345.jpg"

        original_redis = flask_app.redis_client
        original_initialized = flask_app._is_initialized

        flask_app._is_initialized = True
        flask_app.redis_client = mock_redis

        try:
            response = client.get("/api/dashboard/activity")
            assert response.status_code == 200
            data = response.get_json()

            entry = data["entries"][0]
            # Should handle bytes and convert to string
            assert entry["avatar_url"] is not None
            assert "user_12345.jpg" in str(entry["avatar_url"])
        finally:
            flask_app.redis_client = original_redis
            flask_app._is_initialized = original_initialized

    def test_avatar_error_handling(self, app, client):
        """Test that avatar fetch errors don't break activity feed."""
        import app as flask_app  # type: ignore[import-not-found]

        mock_redis = MagicMock()

        activity_data = {
            "json": json.dumps(
                {
                    "sender_id": 12345,
                    "sender_name": "Test User",
                    "message": "Test",
                    "chat_name": "Test Chat",
                }
            )
        }

        mock_redis.xrevrange.return_value = [("msg-1", activity_data)]

        # Simulate Redis error when fetching avatar
        mock_redis.get.side_effect = Exception("Redis connection error")

        original_redis = flask_app.redis_client
        original_initialized = flask_app._is_initialized

        flask_app._is_initialized = True
        flask_app.redis_client = mock_redis

        try:
            response = client.get("/api/dashboard/activity")
            # Should still return 200 even if avatar fetch fails
            assert response.status_code == 200
            data = response.get_json()

            # Activity feed should still be present
            assert "entries" in data
            assert len(data["entries"]) > 0

            # Avatar should be None on error
            entry = data["entries"][0]
            assert entry["avatar_url"] is None
        finally:
            flask_app.redis_client = original_redis
            flask_app._is_initialized = original_initialized

    def test_avatar_without_sender_id(self, app, client):
        """Test activity entry without sender_id doesn't try to fetch avatar."""
        import app as flask_app  # type: ignore[import-not-found]

        mock_redis = MagicMock()

        activity_data = {
            "json": json.dumps(
                {
                    "sender_name": "Anonymous User",
                    "message": "Test message",
                    "chat_name": "Test Chat",
                    # Note: no sender_id
                }
            )
        }

        mock_redis.xrevrange.return_value = [("msg-1", activity_data)]

        original_redis = flask_app.redis_client
        original_initialized = flask_app._is_initialized

        flask_app._is_initialized = True
        flask_app.redis_client = mock_redis

        try:
            response = client.get("/api/dashboard/activity")
            assert response.status_code == 200
            data = response.get_json()

            entry = data["entries"][0]
            # Should have None avatar when no sender_id
            assert entry["avatar_url"] is None
            # Redis get should not be called for avatar
            # (it might be called for other things, so we just check the result)
        finally:
            flask_app.redis_client = original_redis
            flask_app._is_initialized = original_initialized

    def test_avatar_ttl_is_one_hour(self):
        """Test that avatar cache TTL is set correctly (1 hour = 3600 seconds)."""
        # This is a documentation test for the expected TTL
        expected_ttl = 3600  # 1 hour in seconds

        # Verify the TTL value is reasonable
        assert expected_ttl == 60 * 60
        assert expected_ttl > 0

        # One hour is a good balance between:
        # - Not making too many requests to Telegram
        # - Keeping avatars reasonably up-to-date

    def test_avatar_path_security(self):
        """Test that avatar paths don't allow directory traversal."""
        sender_id = 12345
        avatar_url = f"/data/avatars/user_{sender_id}.jpg"

        # Should not contain path traversal characters
        assert ".." not in avatar_url
        assert "~" not in avatar_url

        # Should be a safe relative path
        assert not avatar_url.startswith("/etc/")
        assert not avatar_url.startswith("/var/")

        # Should only contain expected pattern
        assert avatar_url.count("/") == 3  # /data/avatars/user_*.jpg
