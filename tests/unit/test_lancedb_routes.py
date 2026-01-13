"""
Unit tests for LanceDB routes module.

Tests cover:
- List databases endpoint
- Get database info endpoint
- List documents endpoint
- Path resolution logic
- Helper functions
"""

from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from soliplex.ingester.lib.config import Settings
from soliplex.ingester.server.routes.lancedb import create_app
from soliplex.ingester.server.routes.lancedb import format_bytes
from soliplex.ingester.server.routes.lancedb import get_folder_size
from soliplex.ingester.server.routes.lancedb import resolve_lancedb_path


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_format_bytes_bytes(self):
        """Test formatting bytes."""
        assert format_bytes(500) == "500.00 B"

    def test_format_bytes_kilobytes(self):
        """Test formatting kilobytes."""
        assert format_bytes(1024) == "1.00 KB"

    def test_format_bytes_megabytes(self):
        """Test formatting megabytes."""
        assert format_bytes(1024 * 1024) == "1.00 MB"

    def test_format_bytes_gigabytes(self):
        """Test formatting gigabytes."""
        assert format_bytes(1024 * 1024 * 1024) == "1.00 GB"

    def test_format_bytes_terabytes(self):
        """Test formatting terabytes."""
        assert format_bytes(1024 * 1024 * 1024 * 1024) == "1.00 TB"

    def test_format_bytes_petabytes(self):
        """Test formatting petabytes."""
        assert format_bytes(1024 * 1024 * 1024 * 1024 * 1024) == "1.00 PB"

    def test_format_bytes_zero(self):
        """Test formatting zero bytes."""
        assert format_bytes(0) == "0.00 B"

    def test_get_folder_size_permission_error(self, tmp_path):
        """Test getting size when permission error occurs."""
        # Mock rglob to raise PermissionError
        with patch.object(Path, "rglob", side_effect=PermissionError("Access denied")):
            result = get_folder_size(tmp_path)
            assert result == 0

    def test_create_app_with_db_path(self, tmp_path):
        """Test create_app with explicit database path."""
        db_path = tmp_path / "test.lancedb"

        mock_config = MagicMock()
        mock_haiku_app = MagicMock()

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_config", return_value=mock_config),
            patch("soliplex.ingester.server.routes.lancedb.HaikuRAGApp", return_value=mock_haiku_app) as mock_cls,
        ):
            result = create_app(db=db_path, read_only=True)

            mock_cls.assert_called_once_with(
                db_path=db_path,
                config=mock_config,
                read_only=True,
            )
            assert result == mock_haiku_app

    def test_create_app_without_db_path(self):
        """Test create_app uses config default when no path provided."""
        mock_config = MagicMock()
        mock_config.storage.data_dir = Path("/data")
        mock_haiku_app = MagicMock()

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_config", return_value=mock_config),
            patch("soliplex.ingester.server.routes.lancedb.HaikuRAGApp", return_value=mock_haiku_app) as mock_cls,
        ):
            result = create_app()

            mock_cls.assert_called_once_with(
                db_path=Path("/data/haiku.rag.lancedb"),
                config=mock_config,
                read_only=False,
            )
            assert result == mock_haiku_app

    def test_resolve_lancedb_path_with_lancedb_suffix(self):
        """Test path resolution when .lancedb suffix already present."""
        result = resolve_lancedb_path("test.lancedb", "/data/lancedb")
        assert result == Path("/data/lancedb/test.lancedb")

    def test_resolve_lancedb_path_without_suffix(self):
        """Test path resolution - returns db_name joined to lancedb_dir."""
        result = resolve_lancedb_path("mydb", "/data/lancedb")
        assert result == Path("/data/lancedb/mydb")

    def test_resolve_lancedb_path_nested(self):
        """Test path resolution with nested path."""
        result = resolve_lancedb_path("project/subdir", "/data/lancedb")
        assert result == Path("/data/lancedb/project/subdir")

    def test_get_folder_size_empty_folder(self, tmp_path):
        """Test getting size of empty folder."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assert get_folder_size(empty_dir) == 0

    def test_get_folder_size_with_files(self, tmp_path):
        """Test getting size of folder with files."""
        test_dir = tmp_path / "test"
        test_dir.mkdir()
        # Create test file with known content
        (test_dir / "file1.txt").write_bytes(b"x" * 100)
        (test_dir / "file2.txt").write_bytes(b"y" * 200)
        assert get_folder_size(test_dir) == 300

    def test_get_folder_size_nested(self, tmp_path):
        """Test getting size of folder with nested structure."""
        test_dir = tmp_path / "test"
        test_dir.mkdir()
        subdir = test_dir / "subdir"
        subdir.mkdir()
        (test_dir / "file1.txt").write_bytes(b"x" * 100)
        (subdir / "file2.txt").write_bytes(b"y" * 200)
        assert get_folder_size(test_dir) == 300

    def test_get_folder_size_nonexistent(self, tmp_path):
        """Test getting size of non-existent folder."""
        nonexistent = tmp_path / "nonexistent"
        # Should return 0 and not raise
        assert get_folder_size(nonexistent) == 0


class TestListDatabases:
    """Tests for /api/v1/lancedb/list endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client with mocked dependencies."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = False
        settings.auth_trust_proxy_headers = False
        settings.doc_db_url = "sqlite+aiosqlite:///:memory:"
        settings.log_level = "INFO"
        settings.lancedb_dir = "/data/lancedb"

        with patch("soliplex.ingester.lib.wf.runner.start_worker", new_callable=AsyncMock):
            from soliplex.ingester.lib.config import get_settings
            from soliplex.ingester.server import app

            app.dependency_overrides[get_settings] = lambda: settings

            client = TestClient(app, raise_server_exceptions=False)
            yield client, settings

            app.dependency_overrides.clear()

    def test_list_databases_dir_not_exists(self, client):
        """Test listing databases when directory doesn't exist."""
        test_client, settings = client

        with patch("soliplex.ingester.server.routes.lancedb.Path") as mock_path:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = False
            mock_path.return_value = mock_path_instance

            response = test_client.get("/api/v1/lancedb/list")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["databases"] == []
            assert "does not exist" in data.get("message", "")

    def test_list_databases_empty(self, client, tmp_path):
        """Test listing databases when directory is empty."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        with patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings):
            response = test_client.get("/api/v1/lancedb/list")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["database_count"] == 0
            assert data["databases"] == []

    def test_list_databases_with_folders(self, client, tmp_path):
        """Test listing databases with folders present (looks for chunks.lance dirs)."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        # Create test lancedb structures with chunks.lance directories
        db1 = tmp_path / "db1.lancedb" / "chunks.lance"
        db1.mkdir(parents=True)
        (db1 / "data.lance").write_bytes(b"x" * 1000)

        db2 = tmp_path / "db2.lancedb" / "chunks.lance"
        db2.mkdir(parents=True)
        (db2 / "data.lance").write_bytes(b"y" * 2000)

        with patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings):
            response = test_client.get("/api/v1/lancedb/list")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["database_count"] == 2
            assert len(data["databases"]) == 2

            # Check database entries - names are parent paths relative to lancedb_dir
            names = [d["name"] for d in data["databases"]]
            assert "db1.lancedb" in names
            assert "db2.lancedb" in names

            for db in data["databases"]:
                assert "name" in db
                assert "path" in db
                assert "size_bytes" in db
                assert "size_human" in db

    def test_list_databases_ignores_folders_without_chunks_lance(self, client, tmp_path):
        """Test that folders without chunks.lance are ignored."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        # Create a valid lancedb folder with chunks.lance
        valid_db = tmp_path / "valid.lancedb" / "chunks.lance"
        valid_db.mkdir(parents=True)

        # Create folders without chunks.lance (should be ignored)
        invalid_folder = tmp_path / "invalid"
        invalid_folder.mkdir()
        (tmp_path / "readme.txt").write_text("not a database")

        with patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings):
            response = test_client.get("/api/v1/lancedb/list")

            assert response.status_code == 200
            data = response.json()
            assert data["database_count"] == 1
            assert data["databases"][0]["name"] == "valid.lancedb"

    def test_list_databases_handles_permission_error(self, client, tmp_path):
        """Test listing databases when permission error occurs during rglob."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        # Create the directory but mock rglob to raise PermissionError
        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("soliplex.ingester.server.routes.lancedb.Path") as mock_path_class,
        ):
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.rglob.side_effect = PermissionError("Access denied")
            mock_path_instance.__str__ = lambda self: str(tmp_path)
            mock_path_class.return_value = mock_path_instance

            response = test_client.get("/api/v1/lancedb/list")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["databases"] == []


class TestGetInfo:
    """Tests for /api/v1/lancedb/info endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client with mocked dependencies."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = False
        settings.auth_trust_proxy_headers = False
        settings.doc_db_url = "sqlite+aiosqlite:///:memory:"
        settings.log_level = "INFO"
        settings.lancedb_dir = "/data/lancedb"

        with patch("soliplex.ingester.lib.wf.runner.start_worker", new_callable=AsyncMock):
            from soliplex.ingester.lib.config import get_settings
            from soliplex.ingester.server import app

            app.dependency_overrides[get_settings] = lambda: settings

            client = TestClient(app, raise_server_exceptions=False)
            yield client, settings

            app.dependency_overrides.clear()

    def test_get_info_db_not_found(self, client, tmp_path):
        """Test getting info for non-existent database."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        with patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings):
            response = test_client.get("/api/v1/lancedb/info", params={"db": "nonexistent"})

            assert response.status_code == 404
            data = response.json()
            assert data["status"] == "error"
            assert "not found" in data["error"].lower()

    def test_get_info_success(self, client, tmp_path):
        """Test getting info for valid database."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        # Create mock database path (resolve_lancedb_path just joins paths now)
        db_path = tmp_path / "testdb"
        db_path.mkdir(parents=True)

        # Mock list_tables() to return object with .tables attribute
        mock_list_tables_result = MagicMock()
        mock_list_tables_result.tables = ["documents", "chunks", "settings"]

        mock_db_conn = MagicMock()
        mock_db_conn.list_tables.return_value = mock_list_tables_result
        mock_db_conn.open_table.return_value = MagicMock()

        mock_store = MagicMock()
        mock_store.get_stats.return_value = {
            "documents": {"num_rows": 10, "total_bytes": 5000},
            "chunks": {
                "num_rows": 100,
                "total_bytes": 50000,
                "has_vector_index": True,
                "num_indexed_rows": 90,
                "num_unindexed_rows": 10,
            },
        }

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("lancedb.connect", return_value=mock_db_conn),
            patch("haiku.rag.store.engine.Store", return_value=mock_store),
            patch("haiku.rag.config.get_config", return_value=MagicMock()),
            patch("importlib.metadata.version", return_value="0.1.0"),
        ):
            response = test_client.get("/api/v1/lancedb/info", params={"db": "testdb"})

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert "path" in data
            assert "versions" in data
            assert "embeddings" in data
            assert "documents" in data
            assert "chunks" in data
            assert "vector_index" in data
            assert "tables" in data

    def test_get_info_connection_error(self, client, tmp_path):
        """Test getting info when database connection fails."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        # Create mock database path (resolve_lancedb_path just joins paths now)
        db_path = tmp_path / "baddb"
        db_path.mkdir(parents=True)

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("lancedb.connect", side_effect=Exception("Connection failed")),
        ):
            response = test_client.get("/api/v1/lancedb/info", params={"db": "baddb"})

            assert response.status_code == 500
            data = response.json()
            assert data["status"] == "error"
            assert "failed" in data["error"].lower()

    def test_get_info_with_lancedb_suffix(self, client, tmp_path):
        """Test getting info with explicit .lancedb suffix."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        # Create database path with explicit suffix
        db_path = tmp_path / "custom.lancedb"
        db_path.mkdir(parents=True)

        # Mock list_tables() to return object with .tables attribute
        mock_list_tables_result = MagicMock()
        mock_list_tables_result.tables = []

        mock_db_conn = MagicMock()
        mock_db_conn.list_tables.return_value = mock_list_tables_result

        mock_store = MagicMock()
        mock_store.get_stats.return_value = {
            "documents": {"num_rows": 0, "total_bytes": 0},
            "chunks": {"num_rows": 0, "total_bytes": 0, "has_vector_index": False},
        }

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("lancedb.connect", return_value=mock_db_conn),
            patch("haiku.rag.store.engine.Store", return_value=mock_store),
            patch("haiku.rag.config.get_config", return_value=MagicMock()),
            patch("importlib.metadata.version", return_value="0.1.0"),
        ):
            response = test_client.get("/api/v1/lancedb/info", params={"db": "custom.lancedb"})

            assert response.status_code == 200

    def test_get_info_version_exceptions(self, client, tmp_path):
        """Test getting info when version lookups fail."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        db_path = tmp_path / "testdb"
        db_path.mkdir(parents=True)

        mock_list_tables_result = MagicMock()
        mock_list_tables_result.tables = []

        mock_db_conn = MagicMock()
        mock_db_conn.list_tables.return_value = mock_list_tables_result

        mock_store = MagicMock()
        mock_store.get_stats.return_value = {
            "documents": {"num_rows": 0, "total_bytes": 0},
            "chunks": {"num_rows": 0, "total_bytes": 0, "has_vector_index": False},
        }

        def version_side_effect(pkg):
            raise ModuleNotFoundError(f"Package {pkg} not found")

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("lancedb.connect", return_value=mock_db_conn),
            patch("haiku.rag.store.engine.Store", return_value=mock_store),
            patch("haiku.rag.config.get_config", return_value=MagicMock()),
            patch("importlib.metadata.version", side_effect=version_side_effect),
        ):
            response = test_client.get("/api/v1/lancedb/info", params={"db": "testdb"})

            assert response.status_code == 200
            data = response.json()
            assert data["versions"]["lancedb"] == "unknown"
            assert data["versions"]["haiku_rag"] == "unknown"

    def test_get_info_store_stats_exception(self, client, tmp_path):
        """Test getting info when Store.get_stats fails."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        db_path = tmp_path / "testdb"
        db_path.mkdir(parents=True)

        mock_list_tables_result = MagicMock()
        mock_list_tables_result.tables = []

        mock_db_conn = MagicMock()
        mock_db_conn.list_tables.return_value = mock_list_tables_result

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("lancedb.connect", return_value=mock_db_conn),
            patch("haiku.rag.store.engine.Store", side_effect=Exception("Store failed")),
            patch("haiku.rag.config.get_config", return_value=MagicMock()),
            patch("importlib.metadata.version", return_value="0.1.0"),
        ):
            response = test_client.get("/api/v1/lancedb/info", params={"db": "testdb"})

            assert response.status_code == 200
            data = response.json()
            # Should have default stats when Store fails
            assert data["documents"]["count"] == 0
            assert data["chunks"]["count"] == 0

    def test_get_info_settings_table_exception(self, client, tmp_path):
        """Test getting info when reading settings table fails."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        db_path = tmp_path / "testdb"
        db_path.mkdir(parents=True)

        mock_list_tables_result = MagicMock()
        mock_list_tables_result.tables = ["settings"]

        mock_db_conn = MagicMock()
        mock_db_conn.list_tables.return_value = mock_list_tables_result
        mock_db_conn.open_table.side_effect = Exception("Cannot open settings table")

        mock_store = MagicMock()
        mock_store.get_stats.return_value = {
            "documents": {"num_rows": 0, "total_bytes": 0},
            "chunks": {"num_rows": 0, "total_bytes": 0, "has_vector_index": False},
        }

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("lancedb.connect", return_value=mock_db_conn),
            patch("haiku.rag.store.engine.Store", return_value=mock_store),
            patch("haiku.rag.config.get_config", return_value=MagicMock()),
            patch("importlib.metadata.version", return_value="0.1.0"),
        ):
            response = test_client.get("/api/v1/lancedb/info", params={"db": "testdb"})

            assert response.status_code == 200
            data = response.json()
            # Should have default values when settings read fails
            assert data["versions"]["stored_version"] == "unknown"
            assert data["embeddings"]["provider"] is None

    def test_get_info_table_versions_exception(self, client, tmp_path):
        """Test getting info when reading table versions fails."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        db_path = tmp_path / "testdb"
        db_path.mkdir(parents=True)

        mock_list_tables_result = MagicMock()
        mock_list_tables_result.tables = ["documents", "chunks"]

        mock_table = MagicMock()
        mock_table.list_versions.side_effect = Exception("Cannot list versions")

        mock_db_conn = MagicMock()
        mock_db_conn.list_tables.return_value = mock_list_tables_result
        mock_db_conn.open_table.return_value = mock_table

        mock_store = MagicMock()
        mock_store.get_stats.return_value = {
            "documents": {"num_rows": 5, "total_bytes": 1000},
            "chunks": {"num_rows": 50, "total_bytes": 5000, "has_vector_index": True},
        }

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("lancedb.connect", return_value=mock_db_conn),
            patch("haiku.rag.store.engine.Store", return_value=mock_store),
            patch("haiku.rag.config.get_config", return_value=MagicMock()),
            patch("importlib.metadata.version", return_value="0.1.0"),
        ):
            response = test_client.get("/api/v1/lancedb/info", params={"db": "testdb"})

            assert response.status_code == 200
            data = response.json()
            # Versions should be 0 when list_versions fails
            assert data["documents"]["versions"] == 0
            assert data["chunks"]["versions"] == 0


class TestVacuum:
    """Tests for /api/v1/lancedb/vacuum endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client with mocked dependencies."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = False
        settings.auth_trust_proxy_headers = False
        settings.doc_db_url = "sqlite+aiosqlite:///:memory:"
        settings.log_level = "INFO"
        settings.lancedb_dir = "/data/lancedb"

        with patch("soliplex.ingester.lib.wf.runner.start_worker", new_callable=AsyncMock):
            from soliplex.ingester.lib.config import get_settings
            from soliplex.ingester.server import app

            app.dependency_overrides[get_settings] = lambda: settings

            client = TestClient(app, raise_server_exceptions=False)
            yield client, settings

            app.dependency_overrides.clear()

    def test_vacuum_success(self, client, tmp_path):
        """Test vacuum endpoint succeeds."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        mock_app = MagicMock()
        mock_app.vacuum = AsyncMock(return_value=None)

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("soliplex.ingester.server.routes.lancedb.create_app", return_value=mock_app),
        ):
            response = test_client.get("/api/v1/lancedb/vacuum", params={"db": "testdb"})

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            mock_app.vacuum.assert_called_once()

    def test_vacuum_error(self, client, tmp_path):
        """Test vacuum endpoint handles errors."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        mock_app = MagicMock()
        mock_app.vacuum = AsyncMock(side_effect=Exception("Vacuum failed"))

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("soliplex.ingester.server.routes.lancedb.create_app", return_value=mock_app),
        ):
            response = test_client.get("/api/v1/lancedb/vacuum", params={"db": "testdb"})

            assert response.status_code == 500
            data = response.json()
            assert data["status"] == "error"
            assert "Vacuum failed" in data["error"]

    def test_vacuum_requires_auth(self):
        """Test that vacuum endpoint requires authentication."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = True
        settings.auth_trust_proxy_headers = False
        settings.api_key = "test-api-key"
        settings.doc_db_url = "sqlite+aiosqlite:///:memory:"
        settings.log_level = "INFO"
        settings.lancedb_dir = "/data/lancedb"

        with patch("soliplex.ingester.lib.wf.runner.start_worker", new_callable=AsyncMock):
            from soliplex.ingester.lib.config import get_settings
            from soliplex.ingester.server import app

            app.dependency_overrides[get_settings] = lambda: settings

            test_client = TestClient(app, raise_server_exceptions=False)
            response = test_client.get("/api/v1/lancedb/vacuum", params={"db": "test"})
            assert response.status_code == 401

            app.dependency_overrides.clear()


class TestListDocuments:
    """Tests for /api/v1/lancedb/documents endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client with mocked dependencies."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = False
        settings.auth_trust_proxy_headers = False
        settings.doc_db_url = "sqlite+aiosqlite:///:memory:"
        settings.log_level = "INFO"
        settings.lancedb_dir = "/data/lancedb"

        with patch("soliplex.ingester.lib.wf.runner.start_worker", new_callable=AsyncMock):
            from soliplex.ingester.lib.config import get_settings
            from soliplex.ingester.server import app

            app.dependency_overrides[get_settings] = lambda: settings

            client = TestClient(app, raise_server_exceptions=False)
            yield client, settings

            app.dependency_overrides.clear()

    def test_list_documents_db_not_found(self, client, tmp_path):
        """Test listing documents for non-existent database."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        with patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings):
            response = test_client.get("/api/v1/lancedb/documents", params={"db": "nonexistent"})

            assert response.status_code == 404
            data = response.json()
            assert data["status"] == "error"
            assert "not found" in data["error"].lower()

    def test_list_documents_success(self, client, tmp_path):
        """Test listing documents successfully."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        # Create mock database path (resolve_lancedb_path just joins paths now)
        db_path = tmp_path / "testdb"
        db_path.mkdir(parents=True)

        # Create mock documents
        from datetime import datetime

        mock_doc = MagicMock()
        mock_doc.id = "doc-1"
        mock_doc.uri = "/path/to/doc.pdf"
        mock_doc.title = "Test Document"
        mock_doc.created_at = datetime(2024, 1, 1, 12, 0, 0)
        mock_doc.updated_at = datetime(2024, 1, 2, 12, 0, 0)
        mock_doc.chunk_count = 5
        mock_doc.metadata = {"source": "test"}

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.list_documents = AsyncMock(return_value=[mock_doc])

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("haiku.rag.client.HaikuRAG", return_value=mock_client),
            patch("haiku.rag.config.get_config", return_value=MagicMock()),
        ):
            response = test_client.get("/api/v1/lancedb/documents", params={"db": "testdb"})

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["document_count"] == 1
            assert len(data["documents"]) == 1

            doc = data["documents"][0]
            assert doc["id"] == "doc-1"
            assert doc["uri"] == "/path/to/doc.pdf"
            assert doc["title"] == "Test Document"
            assert doc["chunk_count"] == 5

    def test_list_documents_with_pagination(self, client, tmp_path):
        """Test listing documents with limit and offset."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        db_path = tmp_path / "testdb"
        db_path.mkdir(parents=True)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.list_documents = AsyncMock(return_value=[])

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("haiku.rag.client.HaikuRAG", return_value=mock_client),
            patch("haiku.rag.config.get_config", return_value=MagicMock()),
        ):
            response = test_client.get(
                "/api/v1/lancedb/documents",
                params={"db": "testdb", "limit": 10, "offset": 5},
            )

            assert response.status_code == 200
            # Verify the mock was called with the pagination params
            mock_client.list_documents.assert_called_once_with(
                limit=10,
                offset=5,
                filter=None,
            )

    def test_list_documents_with_filter(self, client, tmp_path):
        """Test listing documents with filter."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        db_path = tmp_path / "testdb"
        db_path.mkdir(parents=True)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.list_documents = AsyncMock(return_value=[])

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("haiku.rag.client.HaikuRAG", return_value=mock_client),
            patch("haiku.rag.config.get_config", return_value=MagicMock()),
        ):
            response = test_client.get(
                "/api/v1/lancedb/documents",
                params={"db": "testdb", "filter": "uri LIKE '%test%'"},
            )

            assert response.status_code == 200
            mock_client.list_documents.assert_called_once_with(
                limit=None,
                offset=None,
                filter="uri LIKE '%test%'",
            )

    def test_list_documents_error(self, client, tmp_path):
        """Test listing documents when error occurs."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        db_path = tmp_path / "testdb"
        db_path.mkdir(parents=True)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.list_documents = AsyncMock(side_effect=Exception("Database error"))

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("haiku.rag.client.HaikuRAG", return_value=mock_client),
            patch("haiku.rag.config.get_config", return_value=MagicMock()),
        ):
            response = test_client.get("/api/v1/lancedb/documents", params={"db": "testdb"})

            assert response.status_code == 500
            data = response.json()
            assert data["status"] == "error"
            assert "Database error" in data["error"]

    def test_list_documents_empty(self, client, tmp_path):
        """Test listing documents when database is empty."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        db_path = tmp_path / "emptydb"
        db_path.mkdir(parents=True)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.list_documents = AsyncMock(return_value=[])

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("haiku.rag.client.HaikuRAG", return_value=mock_client),
            patch("haiku.rag.config.get_config", return_value=MagicMock()),
        ):
            response = test_client.get("/api/v1/lancedb/documents", params={"db": "emptydb"})

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["document_count"] == 0
            assert data["documents"] == []

    def test_list_documents_without_metadata(self, client, tmp_path):
        """Test listing documents when documents have no metadata."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        db_path = tmp_path / "testdb"
        db_path.mkdir(parents=True)

        from datetime import datetime

        # Create mock document without metadata attribute
        mock_doc = MagicMock()
        mock_doc.id = "doc-1"
        mock_doc.uri = "/path/to/doc.pdf"
        mock_doc.title = "Test Document"
        mock_doc.created_at = datetime(2024, 1, 1, 12, 0, 0)
        mock_doc.updated_at = datetime(2024, 1, 2, 12, 0, 0)
        mock_doc.chunk_count = 5
        # Set metadata to None to test the branch
        mock_doc.metadata = None

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.list_documents = AsyncMock(return_value=[mock_doc])

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("haiku.rag.client.HaikuRAG", return_value=mock_client),
            patch("haiku.rag.config.get_config", return_value=MagicMock()),
        ):
            response = test_client.get("/api/v1/lancedb/documents", params={"db": "testdb"})

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["document_count"] == 1
            # Document should not have metadata key when metadata is None
            assert "metadata" not in data["documents"][0]

    def test_list_documents_without_optional_fields(self, client, tmp_path):
        """Test listing documents when optional fields are missing."""
        test_client, settings = client
        settings.lancedb_dir = str(tmp_path)

        db_path = tmp_path / "testdb"
        db_path.mkdir(parents=True)

        # Create mock document without optional attributes
        mock_doc = MagicMock(spec=["id", "uri"])
        mock_doc.id = "doc-1"
        mock_doc.uri = "/path/to/doc.pdf"

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.list_documents = AsyncMock(return_value=[mock_doc])

        with (
            patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings),
            patch("haiku.rag.client.HaikuRAG", return_value=mock_client),
            patch("haiku.rag.config.get_config", return_value=MagicMock()),
        ):
            response = test_client.get("/api/v1/lancedb/documents", params={"db": "testdb"})

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            doc = data["documents"][0]
            assert doc["id"] == "doc-1"
            assert doc["uri"] == "/path/to/doc.pdf"
            assert doc["title"] is None
            assert doc["created_at"] is None
            assert doc["updated_at"] is None


class TestAuthenticationRequired:
    """Tests for authentication on lancedb routes."""

    @pytest.fixture
    def client_with_auth(self):
        """Create test client with API key authentication enabled."""
        settings = Mock(spec=Settings)
        settings.api_key_enabled = True
        settings.auth_trust_proxy_headers = False
        settings.api_key = "test-api-key"
        settings.doc_db_url = "sqlite+aiosqlite:///:memory:"
        settings.log_level = "INFO"
        settings.lancedb_dir = "/data/lancedb"

        with patch("soliplex.ingester.lib.wf.runner.start_worker", new_callable=AsyncMock):
            from soliplex.ingester.lib.config import get_settings
            from soliplex.ingester.server import app

            app.dependency_overrides[get_settings] = lambda: settings

            client = TestClient(app, raise_server_exceptions=False)
            yield client, settings

            app.dependency_overrides.clear()

    def test_list_requires_auth(self, client_with_auth):
        """Test that list endpoint requires authentication."""
        test_client, settings = client_with_auth

        response = test_client.get("/api/v1/lancedb/list")
        assert response.status_code == 401

    def test_info_requires_auth(self, client_with_auth):
        """Test that info endpoint requires authentication."""
        test_client, settings = client_with_auth

        response = test_client.get("/api/v1/lancedb/info", params={"db": "test"})
        assert response.status_code == 401

    def test_documents_requires_auth(self, client_with_auth):
        """Test that documents endpoint requires authentication."""
        test_client, settings = client_with_auth

        response = test_client.get("/api/v1/lancedb/documents", params={"db": "test"})
        assert response.status_code == 401

    def test_list_with_valid_token(self, client_with_auth, tmp_path):
        """Test list endpoint with valid authentication."""
        test_client, settings = client_with_auth
        settings.lancedb_dir = str(tmp_path)

        with patch("soliplex.ingester.server.routes.lancedb.get_settings", return_value=settings):
            response = test_client.get(
                "/api/v1/lancedb/list",
                headers={"Authorization": "Bearer test-api-key"},
            )
            assert response.status_code == 200
