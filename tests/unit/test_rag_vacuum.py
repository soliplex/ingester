from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from soliplex.ingester.lib import rag


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.lancedb_dir = "/tmp/lancedb"
    settings.lancedb_hmac_key = MagicMock()
    settings.lancedb_hmac_key.get_secret_value.return_value = "a" * 64
    return settings


# --- _compute_db_hmac ---


def test_compute_db_hmac(tmp_path):
    """Test HMAC computation over files in a directory"""
    db_dir = tmp_path / "testdb"
    db_dir.mkdir()
    (db_dir / "file_a.lance").write_bytes(b"data-a")
    (db_dir / "file_b.lance").write_bytes(b"data-b")

    key = b"k" * 64
    result = rag._compute_db_hmac(db_dir, key)

    assert isinstance(result, str)
    assert len(result) == 128  # SHA-512 hex digest


def test_compute_db_hmac_deterministic(tmp_path):
    """Same files produce the same HMAC"""
    db_dir = tmp_path / "testdb"
    db_dir.mkdir()
    (db_dir / "a.txt").write_bytes(b"hello")
    (db_dir / "b.txt").write_bytes(b"world")

    key = b"k" * 64
    assert rag._compute_db_hmac(db_dir, key) == rag._compute_db_hmac(db_dir, key)


def test_compute_db_hmac_changes_on_file_change(tmp_path):
    """HMAC changes when file content changes"""
    db_dir = tmp_path / "testdb"
    db_dir.mkdir()
    f = db_dir / "data.lance"
    f.write_bytes(b"original")

    key = b"k" * 64
    hash1 = rag._compute_db_hmac(db_dir, key)

    f.write_bytes(b"modified")
    hash2 = rag._compute_db_hmac(db_dir, key)

    assert hash1 != hash2


def test_compute_db_hmac_skips_subdirectories(tmp_path):
    """Directories are not fed into the HMAC, only files"""
    db_dir = tmp_path / "testdb"
    db_dir.mkdir()
    sub = db_dir / "subdir"
    sub.mkdir()
    (db_dir / "file.txt").write_bytes(b"data")

    key = b"k" * 64
    # Should not raise; subdir is silently skipped
    result = rag._compute_db_hmac(db_dir, key)
    assert isinstance(result, str)


# --- sign_db ---


def test_sign_db(tmp_path, mock_settings):
    """Test that sign_db writes an .hmac sidecar file"""
    mock_settings.lancedb_dir = str(tmp_path)
    db_dir = tmp_path / "mydb"
    db_dir.mkdir()
    (db_dir / "data.lance").write_bytes(b"test-data")

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        rag.sign_db("mydb")

    hmac_file = tmp_path / "mydb.hmac"
    assert hmac_file.exists()
    content = hmac_file.read_text()
    assert len(content) == 128


def test_sign_db_invalid_key_length(tmp_path, mock_settings):
    """Test that sign_db raises ValueError for wrong key length"""
    mock_settings.lancedb_dir = str(tmp_path)
    mock_settings.lancedb_hmac_key.get_secret_value.return_value = "short"

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        with pytest.raises(ValueError, match="64 bytes"):
            rag.sign_db("mydb")


# --- verify_db ---


def test_verify_db_passes(tmp_path, mock_settings):
    """Test that verify_db returns True for a valid signature"""
    mock_settings.lancedb_dir = str(tmp_path)
    db_dir = tmp_path / "mydb"
    db_dir.mkdir()
    (db_dir / "data.lance").write_bytes(b"test-data")

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        # Sign first
        rag.sign_db("mydb")
        # Verify
        assert rag.verify_db("mydb") is True


def test_verify_db_fails_on_tampered_data(tmp_path, mock_settings):
    """Test that verify_db raises ValueError when data has changed"""
    mock_settings.lancedb_dir = str(tmp_path)
    db_dir = tmp_path / "mydb"
    db_dir.mkdir()
    data_file = db_dir / "data.lance"
    data_file.write_bytes(b"original")

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        rag.sign_db("mydb")
        # Tamper with the data
        data_file.write_bytes(b"tampered")
        with pytest.raises(ValueError, match="HMAC mismatch"):
            rag.verify_db("mydb")


def test_verify_db_missing_hmac_file(tmp_path, mock_settings):
    """Test that verify_db raises FileNotFoundError when .hmac is missing"""
    mock_settings.lancedb_dir = str(tmp_path)
    db_dir = tmp_path / "mydb"
    db_dir.mkdir()

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        with pytest.raises(FileNotFoundError, match="No HMAC file"):
            rag.verify_db("mydb")


def test_verify_db_invalid_key_length(tmp_path, mock_settings):
    """Test that verify_db raises ValueError for wrong key length"""
    mock_settings.lancedb_dir = str(tmp_path)
    mock_settings.lancedb_hmac_key.get_secret_value.return_value = "short"

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        with pytest.raises(ValueError, match="64 bytes"):
            rag.verify_db("mydb")


# --- _resolve_db_path ---


def test_resolve_db_path_direct(tmp_path, mock_settings):
    """Test resolving a direct database path"""
    mock_settings.lancedb_dir = str(tmp_path)
    db_dir = tmp_path / "mydb"
    db_dir.mkdir()
    (db_dir / "data.lance").write_bytes(b"x")

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        result = rag._resolve_db_path("mydb")
        assert result == db_dir


def test_resolve_db_path_with_haiku_subfolder(tmp_path, mock_settings):
    """Test resolving when haiku.rag.lancedb subfolder exists"""
    mock_settings.lancedb_dir = str(tmp_path)
    db_dir = tmp_path / "mydb"
    db_dir.mkdir()
    subdir = db_dir / "haiku.rag.lancedb"
    subdir.mkdir()
    (subdir / "data.lance").write_bytes(b"x")

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        result = rag._resolve_db_path("mydb")
        assert result == subdir


def test_resolve_db_path_not_found(tmp_path, mock_settings):
    """Test that _resolve_db_path raises FileNotFoundError"""
    mock_settings.lancedb_dir = str(tmp_path)

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        with pytest.raises(FileNotFoundError, match="does not exist"):
            rag._resolve_db_path("nonexistent")


# --- vacuum_db ---


@pytest.mark.asyncio
async def test_vacuum_db(tmp_path, mock_settings):
    """Test vacuum_db calls app.vacuum()"""
    mock_settings.lancedb_dir = str(tmp_path)
    db_dir = tmp_path / "mydb"
    db_dir.mkdir()
    (db_dir / "data.lance").write_bytes(b"x")

    mock_app = MagicMock()
    mock_app.vacuum = AsyncMock()

    with (
        patch(
            "soliplex.ingester.lib.rag.get_settings",
            return_value=mock_settings,
        ),
        patch("soliplex.ingester.lib.rag.get_config") as mock_get_config,
        patch(
            "soliplex.ingester.lib.rag.HaikuRAGApp",
            return_value=mock_app,
        ),
    ):
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config

        await rag.vacuum_db("mydb")

        mock_app.vacuum.assert_awaited_once()
        assert mock_config.storage.vacuum_retention_seconds == 0


@pytest.mark.asyncio
async def test_vacuum_db_not_found(tmp_path, mock_settings):
    """Test vacuum_db raises FileNotFoundError for missing db"""
    mock_settings.lancedb_dir = str(tmp_path)

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        with pytest.raises(FileNotFoundError):
            await rag.vacuum_db("nonexistent")


@pytest.mark.asyncio
async def test_vacuum_db_with_migration(tmp_path, mock_settings):
    """Test vacuum_db runs migration when MigrationRequiredError is raised"""
    from haiku.rag.store.exceptions import MigrationRequiredError

    mock_settings.lancedb_dir = str(tmp_path)
    db_dir = tmp_path / "mydb"
    db_dir.mkdir()
    (db_dir / "data.lance").write_bytes(b"x")

    mock_app = MagicMock()
    # First vacuum call raises, second succeeds
    mock_app.vacuum = AsyncMock(side_effect=[MigrationRequiredError("need migrate"), None])
    mock_app.migrate.return_value = ["migration-1"]

    with (
        patch(
            "soliplex.ingester.lib.rag.get_settings",
            return_value=mock_settings,
        ),
        patch("soliplex.ingester.lib.rag.get_config", return_value=MagicMock()),
        patch(
            "soliplex.ingester.lib.rag.HaikuRAGApp",
            return_value=mock_app,
        ),
    ):
        await rag.vacuum_db("mydb")

        mock_app.migrate.assert_called_once()
        assert mock_app.vacuum.await_count == 2


@pytest.mark.asyncio
async def test_vacuum_db_with_sign(tmp_path, mock_settings):
    """Test vacuum_db calls sign_db when sign=True"""
    mock_settings.lancedb_dir = str(tmp_path)
    db_dir = tmp_path / "mydb"
    db_dir.mkdir()
    (db_dir / "data.lance").write_bytes(b"x")

    mock_app = MagicMock()
    mock_app.vacuum = AsyncMock()

    with (
        patch(
            "soliplex.ingester.lib.rag.get_settings",
            return_value=mock_settings,
        ),
        patch("soliplex.ingester.lib.rag.get_config", return_value=MagicMock()),
        patch(
            "soliplex.ingester.lib.rag.HaikuRAGApp",
            return_value=mock_app,
        ),
        patch("soliplex.ingester.lib.rag.sign_db") as mock_sign,
    ):
        await rag.vacuum_db("mydb", sign=True)

        mock_sign.assert_called_once_with("mydb")


@pytest.mark.asyncio
async def test_vacuum_db_without_sign(tmp_path, mock_settings):
    """Test vacuum_db does not call sign_db when sign=False"""
    mock_settings.lancedb_dir = str(tmp_path)
    db_dir = tmp_path / "mydb"
    db_dir.mkdir()
    (db_dir / "data.lance").write_bytes(b"x")

    mock_app = MagicMock()
    mock_app.vacuum = AsyncMock()

    with (
        patch(
            "soliplex.ingester.lib.rag.get_settings",
            return_value=mock_settings,
        ),
        patch("soliplex.ingester.lib.rag.get_config", return_value=MagicMock()),
        patch(
            "soliplex.ingester.lib.rag.HaikuRAGApp",
            return_value=mock_app,
        ),
        patch("soliplex.ingester.lib.rag.sign_db") as mock_sign,
    ):
        await rag.vacuum_db("mydb", sign=False)

        mock_sign.assert_not_called()


# --- list_dbs ---


def test_list_dbs_empty(tmp_path, mock_settings):
    """Test list_dbs returns empty when no databases exist"""
    mock_settings.lancedb_dir = str(tmp_path)

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        assert rag.list_dbs() == []


def test_list_dbs_nonexistent_dir(mock_settings):
    """Test list_dbs returns empty when lancedb_dir doesn't exist"""
    mock_settings.lancedb_dir = "/nonexistent/path"

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        assert rag.list_dbs() == []


def test_list_dbs_finds_databases(tmp_path, mock_settings):
    """Test list_dbs discovers database directories"""
    mock_settings.lancedb_dir = str(tmp_path)

    # Non-empty dir (treated as a db)
    db1 = tmp_path / "db_alpha"
    db1.mkdir()
    (db1 / "data.lance").write_bytes(b"x")

    # Dir with haiku.rag.lancedb subfolder
    db2 = tmp_path / "db_beta"
    db2.mkdir()
    (db2 / "haiku.rag.lancedb").mkdir()

    # Empty dir (should be skipped)
    (tmp_path / "empty_dir").mkdir()

    # Plain file (should be skipped)
    (tmp_path / "not_a_db.txt").write_text("hello")

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        result = rag.list_dbs()
        assert "db_alpha" in result
        assert "db_beta" in result
        assert "empty_dir" not in result
        assert "not_a_db.txt" not in result


def test_list_dbs_skips_hmac_files(tmp_path, mock_settings):
    """Test list_dbs skips .hmac sidecar files"""
    mock_settings.lancedb_dir = str(tmp_path)

    db1 = tmp_path / "mydb"
    db1.mkdir()
    (db1 / "data.lance").write_bytes(b"x")

    # .hmac is a file, not a directory, so it won't match is_dir()
    # but test the suffix check on directories just in case
    (tmp_path / "mydb.hmac").write_text("abc123")

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        result = rag.list_dbs()
        assert "mydb" in result
        assert "mydb.hmac" not in result


def test_list_dbs_sorted(tmp_path, mock_settings):
    """Test list_dbs returns sorted results"""
    mock_settings.lancedb_dir = str(tmp_path)

    for name in ["charlie", "alpha", "bravo"]:
        d = tmp_path / name
        d.mkdir()
        (d / "data.lance").write_bytes(b"x")

    with patch(
        "soliplex.ingester.lib.rag.get_settings",
        return_value=mock_settings,
    ):
        result = rag.list_dbs()
        assert result == ["alpha", "bravo", "charlie"]


# --- vacuum_all ---


@pytest.mark.asyncio
async def test_vacuum_all(tmp_path, mock_settings):
    """Test vacuum_all vacuums every discovered database"""
    mock_settings.lancedb_dir = str(tmp_path)

    for name in ["db1", "db2"]:
        d = tmp_path / name
        d.mkdir()
        (d / "data.lance").write_bytes(b"x")

    with (
        patch(
            "soliplex.ingester.lib.rag.get_settings",
            return_value=mock_settings,
        ),
        patch("soliplex.ingester.lib.rag.vacuum_db", new_callable=AsyncMock) as mock_vacuum,
    ):
        await rag.vacuum_all(sign=False)

        assert mock_vacuum.await_count == 2
        mock_vacuum.assert_any_await("db1", sign=False)
        mock_vacuum.assert_any_await("db2", sign=False)


@pytest.mark.asyncio
async def test_vacuum_all_with_sign(tmp_path, mock_settings):
    """Test vacuum_all passes sign flag through"""
    mock_settings.lancedb_dir = str(tmp_path)

    d = tmp_path / "db1"
    d.mkdir()
    (d / "data.lance").write_bytes(b"x")

    with (
        patch(
            "soliplex.ingester.lib.rag.get_settings",
            return_value=mock_settings,
        ),
        patch("soliplex.ingester.lib.rag.vacuum_db", new_callable=AsyncMock) as mock_vacuum,
    ):
        await rag.vacuum_all(sign=True)

        mock_vacuum.assert_awaited_once_with("db1", sign=True)


@pytest.mark.asyncio
async def test_vacuum_all_no_databases(tmp_path, mock_settings):
    """Test vacuum_all does nothing when no databases found"""
    mock_settings.lancedb_dir = str(tmp_path)

    with (
        patch(
            "soliplex.ingester.lib.rag.get_settings",
            return_value=mock_settings,
        ),
        patch("soliplex.ingester.lib.rag.vacuum_db", new_callable=AsyncMock) as mock_vacuum,
    ):
        await rag.vacuum_all()

        mock_vacuum.assert_not_awaited()


@pytest.mark.asyncio
async def test_vacuum_all_continues_on_failure(tmp_path, mock_settings):
    """Test vacuum_all continues when one database fails"""
    mock_settings.lancedb_dir = str(tmp_path)

    for name in ["db1", "db2", "db3"]:
        d = tmp_path / name
        d.mkdir()
        (d / "data.lance").write_bytes(b"x")

    with (
        patch(
            "soliplex.ingester.lib.rag.get_settings",
            return_value=mock_settings,
        ),
        patch("soliplex.ingester.lib.rag.vacuum_db", new_callable=AsyncMock) as mock_vacuum,
    ):
        # db2 fails, db1 and db3 should still be vacuumed
        mock_vacuum.side_effect = [None, RuntimeError("boom"), None]

        await rag.vacuum_all(sign=False)

        assert mock_vacuum.await_count == 3
