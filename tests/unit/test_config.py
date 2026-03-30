import logging
import logging.handlers
from unittest.mock import patch

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


def _make_settings(**overrides):
    """Helper to create Settings with sensible defaults."""
    defaults = {"doc_db_url": SecretStr("sqlite+aiosqlite:///test.db")}
    defaults.update(overrides)
    return cfg.Settings(**defaults)


class TestAddSmtpHandler:
    def test_no_smtp_settings_no_handler(self):
        """No SMTPHandler when smtp_host is not set."""
        settings = _make_settings()
        root = logging.getLogger()
        before = len(root.handlers)
        cfg._add_smtp_handler(settings)
        smtp_handlers = [h for h in root.handlers[before:] if isinstance(h, logging.handlers.SMTPHandler)]
        assert smtp_handlers == []

    def test_partial_settings_no_handler(self):
        """No SMTPHandler when smtp_to is missing."""
        settings = _make_settings(
            smtp_host="smtp.example.com",
            smtp_from="a@b.com",
        )
        root = logging.getLogger()
        before = len(root.handlers)
        cfg._add_smtp_handler(settings)
        smtp_handlers = [h for h in root.handlers[before:] if isinstance(h, logging.handlers.SMTPHandler)]
        assert smtp_handlers == []

    def test_full_settings_adds_handler(self):
        """SMTPHandler added with correct level when fully configured."""
        settings = _make_settings(
            smtp_host="smtp.example.com",
            smtp_port=25,
            smtp_from="a@b.com",
            smtp_to=["c@d.com"],
            smtp_subject="Alert",
            smtp_use_tls=False,
            smtp_log_level="ERROR",
            smtp_cooldown=30,
        )
        root = logging.getLogger()
        before = len(root.handlers)
        cfg._add_smtp_handler(settings)
        new_handlers = [h for h in root.handlers[before:] if isinstance(h, logging.handlers.SMTPHandler)]
        assert len(new_handlers) == 1
        assert new_handlers[0].level == logging.ERROR
        root.removeHandler(new_handlers[0])

    def test_idempotency_via_configure_logging(self):
        """Calling configure_logging twice produces exactly one SMTPHandler."""
        settings = _make_settings(
            smtp_host="smtp.example.com",
            smtp_port=25,
            smtp_from="a@b.com",
            smtp_to=["c@d.com"],
            smtp_subject="Alert",
            smtp_use_tls=False,
            smtp_log_level="ERROR",
            smtp_cooldown=30,
        )
        cfg.configure_logging(settings)
        cfg.configure_logging(settings)
        root = logging.getLogger()
        smtp_handlers = [h for h in root.handlers if isinstance(h, logging.handlers.SMTPHandler)]
        assert len(smtp_handlers) == 1
        for h in smtp_handlers:
            root.removeHandler(h)

    def test_invalid_smtp_config_logs_warning(self):
        """Bad SMTP config logs a warning instead of crashing."""
        settings = _make_settings(
            smtp_host="smtp.example.com",
            smtp_from="a@b.com",
            smtp_to=["c@d.com"],
        )
        with (
            patch(
                "soliplex.ingester.lib.config._ThrottledSMTPHandler",
                side_effect=Exception("bad"),
            ),
            patch("soliplex.ingester.lib.config.logger") as mock_logger,
        ):
            cfg._add_smtp_handler(settings)
            mock_logger.warning.assert_called_once()

    def test_json_format_on_smtp_handler(self):
        """SMTP handler uses JsonFormatter when log_format is 'json'."""
        settings = _make_settings(
            log_format="json",
            smtp_host="smtp.example.com",
            smtp_port=25,
            smtp_from="a@b.com",
            smtp_to=["c@d.com"],
            smtp_use_tls=False,
            smtp_cooldown=30,
        )
        root = logging.getLogger()
        before = len(root.handlers)
        cfg._add_smtp_handler(settings)
        new_handlers = [h for h in root.handlers[before:] if isinstance(h, logging.handlers.SMTPHandler)]
        assert len(new_handlers) == 1
        assert isinstance(new_handlers[0].formatter, cfg.JsonFormatter)
        root.removeHandler(new_handlers[0])


class TestThrottledSMTPHandler:
    def test_first_emit_sends(self):
        """First record is always emitted."""
        handler = cfg._ThrottledSMTPHandler(
            mailhost=("localhost", 25),
            fromaddr="a@b.com",
            toaddrs=["c@d.com"],
            subject="test",
            cooldown=30,
        )
        record = logging.LogRecord("test", logging.ERROR, "", 0, "msg", (), None)
        with patch.object(logging.handlers.SMTPHandler, "emit") as mock_emit:
            handler.emit(record)
            mock_emit.assert_called_once_with(record)

    def test_second_emit_within_cooldown_suppressed(self):
        """Second record within cooldown is dropped."""
        handler = cfg._ThrottledSMTPHandler(
            mailhost=("localhost", 25),
            fromaddr="a@b.com",
            toaddrs=["c@d.com"],
            subject="test",
            cooldown=30,
        )
        record = logging.LogRecord("test", logging.ERROR, "", 0, "msg", (), None)
        with patch.object(logging.handlers.SMTPHandler, "emit") as mock_emit:
            handler.emit(record)
            handler.emit(record)
            assert mock_emit.call_count == 1

    def test_emit_after_cooldown_sends(self):
        """Record after cooldown expires is emitted."""
        handler = cfg._ThrottledSMTPHandler(
            mailhost=("localhost", 25),
            fromaddr="a@b.com",
            toaddrs=["c@d.com"],
            subject="test",
            cooldown=30,
        )
        record = logging.LogRecord("test", logging.ERROR, "", 0, "msg", (), None)
        with patch.object(logging.handlers.SMTPHandler, "emit") as mock_emit:
            handler.emit(record)
            handler._last_emit -= 31
            handler.emit(record)
            assert mock_emit.call_count == 2
