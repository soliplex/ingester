import logging
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from soliplex.ingester.lib import dal
from soliplex.ingester.lib import models

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_recursive_listdir(tmp_path):
    """Test recursive_listdir function"""
    # Create test directory structure
    test_dir = tmp_path / "test"
    test_dir.mkdir()
    (test_dir / "file1.txt").write_text("test1")
    subdir = test_dir / "subdir"
    subdir.mkdir()
    (subdir / "file2.txt").write_text("test2")

    files = await dal.recursive_listdir(test_dir)
    assert len(files) == 2
    file_names = [f.name for f in files]
    assert "file1.txt" in file_names
    assert "file2.txt" in file_names


@pytest.mark.asyncio
async def test_read_input_url_file():
    """Test read_input_url with file:// URL"""
    with patch("soliplex.ingester.lib.dal.read_file_url") as mock_read:
        mock_read.return_value = b"test content"
        result = await dal.read_input_url("file:///tmp/test.txt")
        assert result == b"test content"
        mock_read.assert_called_once()


@pytest.mark.asyncio
async def test_read_input_url_s3():
    """Test read_input_url with s3:// URL"""
    with patch("soliplex.ingester.lib.dal.read_s3_url") as mock_read:
        mock_read.return_value = b"test content"
        result = await dal.read_input_url("s3://soliplex-input/key")
        assert result == b"test content"
        mock_read.assert_called_once_with("s3://soliplex-input/key")


@pytest.mark.asyncio
async def test_read_input_url_s3_bucket_mismatch():
    """Test read_input_url with s3:// URL with mismatched bucket"""
    with patch("soliplex.ingester.lib.dal.read_s3_url") as mock_read:
        mock_read.side_effect = ValueError("Bucket does not match configured bucket")
        with pytest.raises(ValueError, match="Bucket does not match configured bucket"):
            await dal.read_input_url("s3://invalid-bucket/key")


@pytest.mark.asyncio
async def test_read_input_url_unknown():
    """Test read_input_url with unknown URL scheme"""
    with pytest.raises(ValueError, match="Unknown uri"):
        await dal.read_input_url("http://example.com/file.txt")


@pytest.mark.asyncio
async def test_read_file_url(tmp_path):
    """Test read_file_url function"""
    test_file = tmp_path / "test.txt"
    test_file.write_bytes(b"test content")
    file_url = test_file.as_uri()

    result = await dal.read_file_url(file_url)
    assert result == b"test content"


@pytest.mark.asyncio
async def test_read_s3_url():
    """Test read_s3_url function"""
    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        with patch("soliplex.ingester.lib.dal.opendal.AsyncOperator") as mock_op_class:
            mock_s3_config = Mock()
            mock_s3_config.bucket = "test-bucket"
            mock_s3_config.endpoint_url = "http://localhost:9000"
            mock_s3_config.access_key_id = "key"
            mock_s3_config.access_secret = "secret"
            mock_s3_config.region = "us-east-1"

            mock_settings_obj = Mock()
            mock_settings_obj.file_store_target = "s3"
            mock_settings_obj.input_s3 = mock_s3_config
            mock_settings.return_value = mock_settings_obj

            mock_op = AsyncMock()
            mock_op.read.return_value = b"s3 content"
            mock_op_class.return_value = mock_op

            result = await dal.read_s3_url("s3://test-bucket/path/to/file.txt")
            assert result == b"s3 content"
            mock_op.read.assert_called_once_with("path/to/file.txt")


@pytest.mark.asyncio
async def test_db_storage_operator_read(db):
    """Test DBStorageOperator read method"""
    op = dal.DBStorageOperator("doc", "test_root")

    # Write a document first
    test_bytes = b"test content"
    await op.write("test_hash", test_bytes)

    # Read it back
    result = await op.read("test_hash")
    assert result == test_bytes


@pytest.mark.asyncio
async def test_db_storage_operator_read_not_found(db):
    """Test DBStorageOperator read method with file not found"""
    op = dal.DBStorageOperator("doc", "test_root")

    with pytest.raises(FileNotFoundError):
        await op.read("nonexistent_hash")


@pytest.mark.asyncio
async def test_db_storage_operator_exists(db):
    """Test DBStorageOperator exists method"""

    op = dal.DBStorageOperator("doc", "test_root")

    # Should not exist initially
    exists = await op.exists("test_hash")
    assert not exists

    # Write a document
    await op.write("test_hash", b"test content")

    # Should exist now
    exists = await op.exists("test_hash")
    assert exists


@pytest.mark.asyncio
async def test_db_storage_operator_write(db):
    """Test DBStorageOperator write method"""
    op = dal.DBStorageOperator("doc", "test_root")

    test_bytes = b"test content"
    await op.write("test_hash", test_bytes)

    # Verify it was written
    result = await op.read("test_hash")
    assert result == test_bytes


@pytest.mark.asyncio
async def test_db_storage_operator_list(db):
    """Test DBStorageOperator list method"""
    op = dal.DBStorageOperator("doc", "test_root")

    # Write multiple documents
    await op.write("hash1", b"content1")
    await op.write("hash2", b"content2")

    # List them
    hashes = await op.list("")
    assert len(hashes) == 2
    assert "hash1" in hashes
    assert "hash2" in hashes


@pytest.mark.asyncio
async def test_db_storage_operator_get_uri():
    """Test DBStorageOperator get_uri method"""

    op = dal.DBStorageOperator("doc", "test_root")

    uri = op.get_uri("test_hash")
    assert uri == "bytes://test_hash"


@pytest.mark.asyncio
async def test_db_storage_operator_delete(db):
    """Test DBStorageOperator delete method"""
    op = dal.DBStorageOperator("doc", "test_root")

    # Write a document
    await op.write("test_hash", b"test content")

    # Verify it exists
    exists = await op.exists("test_hash")
    assert exists

    # Delete it
    await op.delete("test_hash")

    # Verify it's gone
    exists = await op.exists("test_hash")
    assert not exists


@pytest.mark.asyncio
async def test_db_storage_operator_delete_missing(db):
    """Test DBStorageOperator delete method"""
    op = dal.DBStorageOperator("doc", "test_root")

    # Delete a document that doesn't exist
    with pytest.raises(FileNotFoundError):
        await op.delete("test_hash")


@pytest.mark.asyncio
async def test_file_storage_operator_init_relative_path(tmp_path):
    """Test FileStorageOperator initialization with relative path"""
    with patch("os.getcwd", return_value=str(tmp_path)):
        op = dal.FileStorageOperator("relative/path")
        assert op.store_path.startswith(str(tmp_path))


@pytest.mark.asyncio
async def test_file_storage_operator_init_absolute_path(tmp_path):
    """Test FileStorageOperator initialization with absolute path"""
    test_path = tmp_path / "absolute"
    op = dal.FileStorageOperator(str(test_path))
    assert op.store_path == str(test_path)
    assert test_path.exists()


@pytest.mark.asyncio
async def test_file_storage_operator_read(tmp_path):
    """Test FileStorageOperator read method"""
    op = dal.FileStorageOperator(str(tmp_path))
    test_bytes = b"test content"
    await op.write("test_hash_abc", test_bytes)

    result = await op.read("test_hash_abc")
    assert result == test_bytes


@pytest.mark.asyncio
async def test_file_storage_operator_exists(tmp_path):
    """Test FileStorageOperator exists method"""
    op = dal.FileStorageOperator(str(tmp_path))

    # Should not exist initially
    exists = await op.exists("test_hash_xyz")
    assert not exists

    # Write a file
    await op.write("test_hash_xyz", b"test content")

    # Should exist now
    exists = await op.exists("test_hash_xyz")
    assert exists


@pytest.mark.asyncio
async def test_file_storage_operator_write(tmp_path):
    """Test FileStorageOperator write method"""
    op = dal.FileStorageOperator(str(tmp_path))
    test_bytes = b"test content"
    await op.write("test_hash_def", test_bytes)

    # Verify it was written
    result = await op.read("test_hash_def")
    assert result == test_bytes


@pytest.mark.asyncio
async def test_file_storage_operator_delete(tmp_path):
    """Test FileStorageOperator delete method"""
    op = dal.FileStorageOperator(str(tmp_path))

    # Write a file
    await op.write("test_hash_ghi", b"test content")

    # Verify it exists
    exists = await op.exists("test_hash_ghi")
    assert exists

    # Delete it
    await op.delete("test_hash_ghi")

    # Verify it's gone
    exists = await op.exists("test_hash_ghi")
    assert not exists


@pytest.mark.asyncio
async def test_file_storage_operator_list(tmp_path):
    """Test FileStorageOperator list method"""
    op = dal.FileStorageOperator(str(tmp_path))

    # Write multiple files
    await op.write("hash1_ab", b"content1")
    await op.write("hash2_cd", b"content2")

    # List them
    files = await op.list("")
    assert len(files) >= 2


@pytest.mark.asyncio
async def test_file_storage_operator_get_uri(tmp_path):
    """Test FileStorageOperator get_uri method"""
    op = dal.FileStorageOperator(str(tmp_path))
    uri = op.get_uri("test_hash_jkl")
    assert uri.startswith("file://")


def test_get_storage_operator_doc_artifact():
    """Test get_storage_operator with DOC artifact type"""
    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings.return_value = Mock(
            file_store_target="db",
            file_store_dir="/tmp",
            doc_store_dir="docs",
        )
        op = dal.get_storage_operator(models.ArtifactType.DOC)
        assert isinstance(op, dal.DBStorageOperator)


def test_get_storage_operator_fs_target(tmp_path):
    """Test get_storage_operator with fs target"""
    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings_obj = Mock()
        mock_settings_obj.file_store_target = "fs"
        mock_settings_obj.file_store_dir = str(tmp_path)
        mock_settings_obj.document_store_dir = "docs"
        mock_settings.return_value = mock_settings_obj

        op = dal.get_storage_operator(models.ArtifactType.DOC)
        assert isinstance(op, dal.FileStorageOperator)


def test_get_storage_operator_s3_target():
    """Test get_storage_operator with s3 target"""

    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        with patch("soliplex.ingester.lib.dal.opendal.AsyncOperator") as mock_op:
            # Create mock S3 config
            mock_s3_config = Mock()
            mock_s3_config.bucket = "test-bucket"
            mock_s3_config.endpoint_url = "http://localhost:9000"
            mock_s3_config.access_key_id = "key"
            mock_s3_config.access_secret = "secret"
            mock_s3_config.region = "us-east-1"

            mock_settings_obj = Mock()
            mock_settings_obj.file_store_target = "s3"
            mock_settings_obj.s3_doc = mock_s3_config
            mock_settings.return_value = mock_settings_obj

            _ = dal.get_storage_operator(models.ArtifactType.DOC)
            mock_op.assert_called_once()


def test_get_storage_operator_unknown_target():
    """Test get_storage_operator with unknown target"""
    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings.return_value = Mock(file_store_target="unknown")
        with pytest.raises(ValueError, match="Unknown target"):
            dal.get_storage_operator(models.ArtifactType.DOC)


def test_get_storage_operator_requires_step_config():
    """Test get_storage_operator requires step_config for non-DOC artifacts"""
    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings.return_value = Mock(file_store_target="db")
        with pytest.raises(ValueError, match="step_config is required"):
            dal.get_storage_operator(models.ArtifactType.PARSED_MD)


def test_get_storage_operator_validates_artifact_type():
    """Test get_storage_operator validates artifact type against step type"""
    mock_step_config = Mock()
    mock_step_config.step_type = models.WorkflowStepType.INGEST
    mock_step_config.id = 1

    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings.return_value = Mock(
            file_store_target="db",
            file_store_dir="/tmp",
            parsed_md_store_dir="parsed",
        )
        # INGEST step should not produce PARSED_MD artifact
        with pytest.raises(ValueError, match="Artifact type .* is not expected"):
            dal.get_storage_operator(models.ArtifactType.PARSED_MD, step_config=mock_step_config)


def test_get_storage_operator_with_valid_step_config():
    """Test get_storage_operator with valid step config"""
    mock_step_config = Mock()
    mock_step_config.step_type = models.WorkflowStepType.PARSE
    mock_step_config.id = 1

    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings.return_value = Mock(
            file_store_target="db",
            file_store_dir="/tmp",
            parsed_md_store_dir="parsed",
        )
        op = dal.get_storage_operator(models.ArtifactType.PARSED_MD, step_config=mock_step_config)
        assert isinstance(op, dal.DBStorageOperator)


def test_file_operator_store_path():
    op = dal.FileStorageOperator("tmp")
    assert "tmp" in op.store_path

    op = dal.FileStorageOperator("/tmp")
    assert "tmp" in op.store_path


# Tests for validate_s3_settings function
def test_validate_s3_settings_missing_access_key_id():
    """Test validate_s3_settings raises error for missing access_key_id"""
    s3_settings = Mock()
    s3_settings.access_key_id = "default"
    s3_settings.access_secret = "secret"
    s3_settings.region = "us-east-1"
    s3_settings.bucket = "test-bucket"

    with pytest.raises(ValueError, match="s3.access_key_id is required"):
        dal.validate_s3_settings(s3_settings)


def test_validate_s3_settings_empty_access_key_id():
    """Test validate_s3_settings raises error for empty access_key_id"""
    s3_settings = Mock()
    s3_settings.access_key_id = ""
    s3_settings.access_secret = "secret"
    s3_settings.region = "us-east-1"
    s3_settings.bucket = "test-bucket"

    with pytest.raises(ValueError, match="s3.access_key_id is required"):
        dal.validate_s3_settings(s3_settings)


def test_validate_s3_settings_missing_access_secret():
    """Test validate_s3_settings raises error for missing access_secret"""
    s3_settings = Mock()
    s3_settings.access_key_id = "key"
    s3_settings.access_secret = "default"
    s3_settings.region = "us-east-1"
    s3_settings.bucket = "test-bucket"

    with pytest.raises(ValueError, match="s3.access_secret is required"):
        dal.validate_s3_settings(s3_settings)


def test_validate_s3_settings_empty_access_secret():
    """Test validate_s3_settings raises error for empty access_secret"""
    s3_settings = Mock()
    s3_settings.access_key_id = "key"
    s3_settings.access_secret = ""
    s3_settings.region = "us-east-1"
    s3_settings.bucket = "test-bucket"

    with pytest.raises(ValueError, match="s3.access_secret is required"):
        dal.validate_s3_settings(s3_settings)


def test_validate_s3_settings_missing_region():
    """Test validate_s3_settings raises error for missing region"""
    s3_settings = Mock()
    s3_settings.access_key_id = "key"
    s3_settings.access_secret = "secret"
    s3_settings.region = "default"
    s3_settings.bucket = "test-bucket"

    with pytest.raises(ValueError, match="s3.region is required"):
        dal.validate_s3_settings(s3_settings)


def test_validate_s3_settings_empty_region():
    """Test validate_s3_settings raises error for empty region"""
    s3_settings = Mock()
    s3_settings.access_key_id = "key"
    s3_settings.access_secret = "secret"
    s3_settings.region = ""
    s3_settings.bucket = "test-bucket"

    with pytest.raises(ValueError, match="s3.region is required"):
        dal.validate_s3_settings(s3_settings)


def test_validate_s3_settings_missing_bucket():
    """Test validate_s3_settings raises error for missing bucket"""
    s3_settings = Mock()
    s3_settings.access_key_id = "key"
    s3_settings.access_secret = "secret"
    s3_settings.region = "us-east-1"
    s3_settings.bucket = "default"

    with pytest.raises(ValueError, match="s3.bucket is required"):
        dal.validate_s3_settings(s3_settings)


def test_validate_s3_settings_empty_bucket():
    """Test validate_s3_settings raises error for empty bucket"""
    s3_settings = Mock()
    s3_settings.access_key_id = "key"
    s3_settings.access_secret = "secret"
    s3_settings.region = "us-east-1"
    s3_settings.bucket = ""

    with pytest.raises(ValueError, match="s3.bucket is required"):
        dal.validate_s3_settings(s3_settings)


def test_validate_s3_settings_valid():
    """Test validate_s3_settings passes with valid settings"""
    s3_settings = Mock()
    s3_settings.access_key_id = "key"
    s3_settings.access_secret = "secret"
    s3_settings.region = "us-east-1"
    s3_settings.bucket = "test-bucket"

    # Should not raise
    dal.validate_s3_settings(s3_settings)


def test_create_s3_operator():
    """Test create_s3_operator function"""
    with patch("soliplex.ingester.lib.dal.opendal.AsyncOperator") as mock_op:
        s3_settings = Mock()
        s3_settings.access_key_id = "key"
        s3_settings.access_secret = "secret"
        s3_settings.region = "us-east-1"
        s3_settings.bucket = "test-bucket"
        s3_settings.endpoint_url = "http://localhost:9000"

        dal.create_s3_operator(s3_settings, root="/test")

        mock_op.assert_called_once_with(
            "s3",
            bucket="test-bucket",
            endpoint="http://localhost:9000",
            access_key_id="key",
            secret_access_key="secret",
            region="us-east-1",
            root="/test",
        )


@pytest.mark.asyncio
async def test_read_s3_url_bucket_mismatch():
    """Test read_s3_url raises error when bucket doesn't match configured bucket"""
    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_s3_config = Mock()
        mock_s3_config.bucket = "configured-bucket"

        mock_settings_obj = Mock()
        mock_settings_obj.input_s3 = mock_s3_config
        mock_settings.return_value = mock_settings_obj

        with pytest.raises(ValueError, match="bucket .* does not match configured bucket"):
            await dal.read_s3_url("s3://different-bucket/path/to/file.txt")


# Tests for OpenDALAdapter class
@pytest.mark.asyncio
async def test_opendal_adapter_read():
    """Test OpenDALAdapter read method"""
    mock_op = AsyncMock()
    mock_op.read.return_value = b"test content"

    adapter = dal.OpenDALAdapter(mock_op, root="test-root")
    result = await adapter.read("test/path")

    assert result == b"test content"
    mock_op.read.assert_called_once_with("test/path")


@pytest.mark.asyncio
async def test_opendal_adapter_write():
    """Test OpenDALAdapter write method"""
    mock_op = AsyncMock()

    adapter = dal.OpenDALAdapter(mock_op, root="test-root")
    await adapter.write("test/path", b"test content")

    mock_op.write.assert_called_once_with("test/path", b"test content")


@pytest.mark.asyncio
async def test_opendal_adapter_exists():
    """Test OpenDALAdapter exists method"""
    mock_op = AsyncMock()
    mock_op.exists.return_value = True

    adapter = dal.OpenDALAdapter(mock_op, root="test-root")
    result = await adapter.exists("test/path")

    assert result is True
    mock_op.exists.assert_called_once_with("test/path")


@pytest.mark.asyncio
async def test_opendal_adapter_delete():
    """Test OpenDALAdapter delete method"""
    mock_op = AsyncMock()

    adapter = dal.OpenDALAdapter(mock_op, root="test-root")
    await adapter.delete("test/path")

    mock_op.delete.assert_called_once_with("test/path")


@pytest.mark.asyncio
async def test_opendal_adapter_list():
    """Test OpenDALAdapter list method"""
    mock_op = AsyncMock()

    # Create mock entries
    mock_entry1 = Mock()
    mock_entry1.path = "file1.txt"
    mock_entry2 = Mock()
    mock_entry2.path = "file2.txt"

    # Mock the async iterator
    async def mock_list(prefix):
        for entry in [mock_entry1, mock_entry2]:
            yield entry

    mock_op.list.return_value = mock_list("")

    adapter = dal.OpenDALAdapter(mock_op, root="test-root")
    result = await adapter.list("prefix/")

    assert result == ["file1.txt", "file2.txt"]


def test_opendal_adapter_get_uri_with_root():
    """Test OpenDALAdapter get_uri method with root"""
    mock_op = Mock()
    adapter = dal.OpenDALAdapter(mock_op, root="test-root")
    uri = adapter.get_uri("test/path")

    assert uri == "s3://test-root/test/path"


def test_opendal_adapter_get_uri_without_root():
    """Test OpenDALAdapter get_uri method without root"""
    mock_op = Mock()
    adapter = dal.OpenDALAdapter(mock_op, root="")
    uri = adapter.get_uri("test/path")

    assert uri == "s3://test/path"
