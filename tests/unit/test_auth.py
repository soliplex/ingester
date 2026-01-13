"""
Unit tests for authentication module.

Tests cover:
- API key validation
- Bearer token authentication
- OAuth2 Proxy header authentication
- Combined authentication scenarios
- Auth disabled scenarios
"""

from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from soliplex.ingester.lib.auth import AuthenticatedUser
from soliplex.ingester.lib.auth import get_current_user
from soliplex.ingester.lib.auth import get_user_from_proxy_headers
from soliplex.ingester.lib.auth import require_auth
from soliplex.ingester.lib.auth import validate_api_key
from soliplex.ingester.lib.config import Settings


class TestValidateApiKey:
    """Tests for validate_api_key function."""

    def test_valid_api_key(self):
        """Test validation with correct API key."""
        settings = Mock(spec=Settings)
        settings.api_key = "secret-key-123"
        assert validate_api_key("secret-key-123", settings) is True

    def test_invalid_api_key(self):
        """Test validation with incorrect API key."""
        settings = Mock(spec=Settings)
        settings.api_key = "secret-key-123"
        assert validate_api_key("wrong-key", settings) is False

    def test_no_api_key_configured(self):
        """Test validation when no API key is configured."""
        settings = Mock(spec=Settings)
        settings.api_key = None
        assert validate_api_key("any-key", settings) is False

    def test_empty_api_key_configured(self):
        """Test validation when empty API key is configured."""
        settings = Mock(spec=Settings)
        settings.api_key = ""
        # Empty string is falsy, so should return False
        assert validate_api_key("any-key", settings) is False


class TestGetUserFromProxyHeaders:
    """Tests for get_user_from_proxy_headers function."""

    def test_with_x_auth_request_headers(self):
        """Test extraction from X-Auth-Request-* headers."""
        request = Mock()
        request.headers.get = Mock(
            side_effect=lambda h: {
                "X-Auth-Request-User": "testuser",
                "X-Auth-Request-Email": "test@example.com",
                "X-Auth-Request-Groups": "admin,users",
            }.get(h)
        )

        user = get_user_from_proxy_headers(request)

        assert user is not None
        assert user.identity == "testuser"
        assert user.email == "test@example.com"
        assert user.groups == ["admin", "users"]
        assert user.method == "proxy"

    def test_with_x_forwarded_headers(self):
        """Test extraction from X-Forwarded-* headers (fallback)."""
        request = Mock()
        request.headers.get = Mock(
            side_effect=lambda h: {
                "X-Forwarded-User": "forwardeduser",
                "X-Forwarded-Email": "forwarded@example.com",
                "X-Forwarded-Groups": "group1",
            }.get(h)
        )

        user = get_user_from_proxy_headers(request)

        assert user is not None
        assert user.identity == "forwardeduser"
        assert user.email == "forwarded@example.com"
        assert user.groups == ["group1"]

    def test_with_no_user_header(self):
        """Test when no user header is present."""
        request = Mock()
        request.headers.get = Mock(return_value=None)

        user = get_user_from_proxy_headers(request)

        assert user is None

    def test_with_user_but_no_email_or_groups(self):
        """Test when only user header is present."""
        request = Mock()
        request.headers.get = Mock(
            side_effect=lambda h: {
                "X-Auth-Request-User": "minimaluser",
            }.get(h)
        )

        user = get_user_from_proxy_headers(request)

        assert user is not None
        assert user.identity == "minimaluser"
        assert user.email is None
        assert user.groups is None


class TestGetCurrentUser:
    """Tests for get_current_user dependency."""

    @pytest.mark.asyncio
    async def test_auth_disabled_returns_anonymous(self):
        """Test that disabled auth allows anonymous access."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = False
        settings.auth_trust_proxy_headers = False

        request = Mock()
        credentials = None

        user = await get_current_user(request, credentials, settings)

        assert user.identity == "anonymous"
        assert user.method == "none"

    @pytest.mark.asyncio
    async def test_valid_bearer_token(self):
        """Test authentication with valid Bearer token."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = True
        settings.auth_trust_proxy_headers = False
        settings.api_key = "valid-token"

        request = Mock()
        credentials = Mock()
        credentials.credentials = "valid-token"

        user = await get_current_user(request, credentials, settings)

        assert user.identity == "api-client"
        assert user.method == "api-key"

    @pytest.mark.asyncio
    async def test_invalid_bearer_token_raises_401(self):
        """Test that invalid Bearer token raises 401."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = True
        settings.auth_trust_proxy_headers = False
        settings.api_key = "valid-token"

        request = Mock()
        credentials = Mock()
        credentials.credentials = "invalid-token"

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request, credentials, settings)

        assert exc_info.value.status_code == 401
        assert "Invalid token" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_proxy_headers_authentication(self):
        """Test authentication via OAuth2 Proxy headers."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = False
        settings.auth_trust_proxy_headers = True

        request = Mock()
        request.headers.get = Mock(
            side_effect=lambda h: {
                "X-Auth-Request-User": "proxyuser",
                "X-Auth-Request-Email": "proxy@example.com",
            }.get(h)
        )
        credentials = None

        user = await get_current_user(request, credentials, settings)

        assert user.identity == "proxyuser"
        assert user.email == "proxy@example.com"
        assert user.method == "proxy"

    @pytest.mark.asyncio
    async def test_no_proxy_headers_raises_401(self):
        """Test that missing proxy headers raise 401 when required."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = False
        settings.auth_trust_proxy_headers = True

        request = Mock()
        request.headers.get = Mock(return_value=None)
        credentials = None

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request, credentials, settings)

        assert exc_info.value.status_code == 401
        assert "Authentication required" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_bearer_token_takes_priority_over_proxy(self):
        """Test that Bearer token is checked before proxy headers."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = True
        settings.auth_trust_proxy_headers = True
        settings.api_key = "api-token"

        request = Mock()
        request.headers.get = Mock(
            side_effect=lambda h: {
                "X-Auth-Request-User": "proxyuser",
            }.get(h)
        )
        credentials = Mock()
        credentials.credentials = "api-token"

        user = await get_current_user(request, credentials, settings)

        # Bearer token should be used, not proxy headers
        assert user.identity == "api-client"
        assert user.method == "api-key"

    @pytest.mark.asyncio
    async def test_fallback_to_proxy_when_no_bearer(self):
        """Test fallback to proxy headers when no Bearer token provided."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = True
        settings.auth_trust_proxy_headers = True
        settings.api_key = "api-token"

        request = Mock()
        request.headers.get = Mock(
            side_effect=lambda h: {
                "X-Auth-Request-User": "proxyuser",
                "X-Auth-Request-Email": "proxy@example.com",
            }.get(h)
        )
        credentials = None  # No Bearer token

        user = await get_current_user(request, credentials, settings)

        # Should fall back to proxy headers
        assert user.identity == "proxyuser"
        assert user.method == "proxy"

    @pytest.mark.asyncio
    async def test_both_enabled_neither_provided_raises_401(self):
        """Test 401 when both methods enabled but neither credentials provided."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = True
        settings.auth_trust_proxy_headers = True
        settings.api_key = "api-token"

        request = Mock()
        request.headers.get = Mock(return_value=None)
        credentials = None

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request, credentials, settings)

        assert exc_info.value.status_code == 401
        assert "Bearer token or OAuth2 login" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_api_key_only_no_token_raises_401(self):
        """Test 401 when only API key auth enabled and no token provided."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = True
        settings.auth_trust_proxy_headers = False
        settings.api_key = "api-token"

        request = Mock()
        credentials = None

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request, credentials, settings)

        assert exc_info.value.status_code == 401
        assert "Bearer token required" in exc_info.value.detail


class TestRequireAuth:
    """Tests for require_auth dependency."""

    @pytest.mark.asyncio
    async def test_rejects_anonymous_user(self):
        """Test that anonymous users are rejected."""
        anonymous_user = AuthenticatedUser(identity="anonymous", method="none")

        with pytest.raises(HTTPException) as exc_info:
            await require_auth(anonymous_user)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_allows_api_key_user(self):
        """Test that API key authenticated users are allowed."""
        api_user = AuthenticatedUser(identity="api-client", method="api-key")

        result = await require_auth(api_user)

        assert result == api_user

    @pytest.mark.asyncio
    async def test_allows_proxy_user(self):
        """Test that proxy authenticated users are allowed."""
        proxy_user = AuthenticatedUser(identity="proxyuser", email="proxy@example.com", method="proxy")

        result = await require_auth(proxy_user)

        assert result == proxy_user


class TestAuthIntegration:
    """Integration tests for authentication with FastAPI TestClient."""

    @pytest.fixture
    def app_with_auth_enabled(self):
        """Create app with API key authentication enabled."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = True
        settings.auth_trust_proxy_headers = False
        settings.api_key = "test-api-key"
        settings.doc_db_url = "sqlite+aiosqlite:///:memory:"
        settings.log_level = "INFO"

        with patch("soliplex.ingester.lib.wf.runner.start_worker", new_callable=AsyncMock):
            from soliplex.ingester.lib.config import get_settings
            from soliplex.ingester.server import app

            # Override the dependency in the app
            app.dependency_overrides[get_settings] = lambda: settings

            client = TestClient(app, raise_server_exceptions=False)
            yield client, settings

            # Clean up overrides
            app.dependency_overrides.clear()

    @pytest.fixture
    def app_with_auth_disabled(self):
        """Create app with authentication disabled."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = False
        settings.auth_trust_proxy_headers = False
        settings.api_key = None
        settings.doc_db_url = "sqlite+aiosqlite:///:memory:"
        settings.log_level = "INFO"

        with patch("soliplex.ingester.lib.wf.runner.start_worker", new_callable=AsyncMock):
            from soliplex.ingester.lib.config import get_settings
            from soliplex.ingester.server import app

            app.dependency_overrides[get_settings] = lambda: settings

            client = TestClient(app, raise_server_exceptions=False)
            yield client, settings

            app.dependency_overrides.clear()

    @pytest.fixture
    def app_with_proxy_auth(self):
        """Create app with proxy header authentication enabled."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = False
        settings.auth_trust_proxy_headers = True
        settings.api_key = None
        settings.doc_db_url = "sqlite+aiosqlite:///:memory:"
        settings.log_level = "INFO"

        with patch("soliplex.ingester.lib.wf.runner.start_worker", new_callable=AsyncMock):
            from soliplex.ingester.lib.config import get_settings
            from soliplex.ingester.server import app

            app.dependency_overrides[get_settings] = lambda: settings

            client = TestClient(app, raise_server_exceptions=False)
            yield client, settings

            app.dependency_overrides.clear()

    def test_request_with_valid_bearer_token(self, app_with_auth_enabled):
        """Test API request with valid Bearer token."""
        client, settings = app_with_auth_enabled

        with patch("soliplex.ingester.server.routes.batch.operations.list_batches") as mock_list:
            mock_list.return_value = []
            response = client.get("/api/v1/batch/", headers={"Authorization": "Bearer test-api-key"})
            assert response.status_code == 200

    def test_request_with_invalid_bearer_token(self, app_with_auth_enabled):
        """Test API request with invalid Bearer token."""
        client, settings = app_with_auth_enabled

        response = client.get("/api/v1/batch/", headers={"Authorization": "Bearer wrong-key"})
        assert response.status_code == 401

    def test_request_without_token_when_required(self, app_with_auth_enabled):
        """Test API request without token when auth is required."""
        client, settings = app_with_auth_enabled

        response = client.get("/api/v1/batch/")
        assert response.status_code == 401

    def test_request_without_token_when_auth_disabled(self, app_with_auth_disabled):
        """Test API request without token when auth is disabled."""
        client, settings = app_with_auth_disabled

        with patch("soliplex.ingester.server.routes.batch.operations.list_batches") as mock_list:
            mock_list.return_value = []
            response = client.get("/api/v1/batch/")
            assert response.status_code == 200

    def test_request_with_proxy_headers(self, app_with_proxy_auth):
        """Test API request with OAuth2 Proxy headers."""
        client, settings = app_with_proxy_auth

        with patch("soliplex.ingester.server.routes.batch.operations.list_batches") as mock_list:
            mock_list.return_value = []
            response = client.get(
                "/api/v1/batch/",
                headers={
                    "X-Auth-Request-User": "testuser",
                    "X-Auth-Request-Email": "test@example.com",
                },
            )
            assert response.status_code == 200

    def test_request_without_proxy_headers_when_required(self, app_with_proxy_auth):
        """Test API request without proxy headers when required."""
        client, settings = app_with_proxy_auth

        response = client.get("/api/v1/batch/")
        assert response.status_code == 401


class TestAuthenticatedUserDataclass:
    """Tests for AuthenticatedUser dataclass."""

    def test_default_values(self):
        """Test AuthenticatedUser default values."""
        user = AuthenticatedUser(identity="testuser")

        assert user.identity == "testuser"
        assert user.email is None
        assert user.groups is None
        assert user.method == "none"

    def test_full_initialization(self):
        """Test AuthenticatedUser with all fields."""
        user = AuthenticatedUser(identity="testuser", email="test@example.com", groups=["admin", "users"], method="proxy")

        assert user.identity == "testuser"
        assert user.email == "test@example.com"
        assert user.groups == ["admin", "users"]
        assert user.method == "proxy"
