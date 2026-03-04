import logging

from pydantic import SecretStr

import soliplex.ingester.lib.config as cfg

logger = logging.getLogger(__name__)


def test_config_settings(monkeypatch):
    logger.info("test_config_settings started")
    settings = cfg.get_settings()
    assert settings
    logger.info(f"settings={settings}")


def test_password_substitution_with_xxxx_placeholder():
    """Test that XXXX placeholder in doc_db_url is replaced with doc_db_password."""
    # Create settings with XXXX placeholder in URL and a separate password
    settings = cfg.Settings(
        doc_db_url=SecretStr("postgresql+psycopg://user:XXXX@localhost:5432/testdb"),
        doc_db_password=SecretStr("my_secret_pass"),
    )

    # Verify the password was substituted
    expected_url = "postgresql+psycopg://user:my_secret_pass@localhost:5432/testdb"
    assert settings.doc_db_url.get_secret_value() == expected_url


def test_password_substitution_without_xxxx_placeholder():
    """Test that doc_db_url is unchanged when XXXX placeholder is not present."""
    original_url = "postgresql+psycopg://user:hardcoded@localhost:5432/testdb"
    settings = cfg.Settings(doc_db_url=SecretStr(original_url), doc_db_password=SecretStr("my_secret_pass"))

    # Verify the URL was not modified
    assert settings.doc_db_url.get_secret_value() == original_url


def test_password_substitution_empty_password():
    """Test that XXXX placeholder remains when doc_db_password is empty."""
    original_url = "postgresql+psycopg://user:XXXX@localhost:5432/testdb"
    settings = cfg.Settings(doc_db_url=SecretStr(original_url), doc_db_password=SecretStr(""))

    # Verify the URL was not modified when password is empty
    assert settings.doc_db_url.get_secret_value() == original_url


def test_password_substitution_multiple_xxxx():
    """Test that multiple XXXX occurrences are all replaced."""
    settings = cfg.Settings(
        doc_db_url=SecretStr("postgresql+psycopg://user:XXXX@localhost:5432/testdb?password=XXXX"),
        doc_db_password=SecretStr("my_secret_pass"),
    )

    # Verify all occurrences were substituted
    expected_url = "postgresql+psycopg://user:my_secret_pass@localhost:5432/testdb?password=my_secret_pass"
    assert settings.doc_db_url.get_secret_value() == expected_url


def test_password_substitution_none_password():
    """Test that XXXX placeholder remains when doc_db_password is None."""
    original_url = "postgresql+psycopg://user:XXXX@localhost:5432/testdb"
    settings = cfg.Settings(doc_db_url=SecretStr(original_url), doc_db_password=None)

    # Verify the URL was not modified when password is None
    assert settings.doc_db_url.get_secret_value() == original_url


def test_production_mode_requires_auth():
    """Test that production mode requires authentication to be enabled."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc_info:
        cfg.Settings(
            doc_db_url=SecretStr("sqlite+aiosqlite:///test.db"),
            production_mode=True,
            api_key_enabled=False,
            auth_trust_proxy_headers=False,
        )

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert "authentication" in errors[0]["msg"].lower()


def test_production_mode_with_api_key_auth():
    """Test that production mode works with API key authentication."""
    settings = cfg.Settings(
        doc_db_url=SecretStr("sqlite+aiosqlite:///test.db"),
        production_mode=True,
        api_key_enabled=True,
        api_key=SecretStr("test-key"),
    )

    assert settings.production_mode is True
    assert settings.api_key_enabled is True


def test_production_mode_with_proxy_auth():
    """Test that production mode works with proxy authentication."""
    settings = cfg.Settings(
        doc_db_url=SecretStr("sqlite+aiosqlite:///test.db"), production_mode=True, auth_trust_proxy_headers=True
    )

    assert settings.production_mode is True
    assert settings.auth_trust_proxy_headers is True


def test_development_mode_without_auth():
    """Test that development mode (default) allows no authentication."""
    settings = cfg.Settings(
        doc_db_url=SecretStr("sqlite+aiosqlite:///test.db"),
        production_mode=False,
        api_key_enabled=False,
        auth_trust_proxy_headers=False,
    )

    assert settings.production_mode is False
    assert settings.api_key_enabled is False
    assert settings.auth_trust_proxy_headers is False
