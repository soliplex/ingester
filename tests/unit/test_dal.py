import asyncio
import base64
import errno
import logging
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
import zstandard
from cryptography.fernet import InvalidToken

from soliplex.ingester.lib import dal
from soliplex.ingester.lib import models
from soliplex.ingester.lib.config import ProtectionLevel

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


@pytest.mark.asyncio
async def test_file_storage_operator_write_disk_space_ok(tmp_path):
    """write succeeds when disk_usage reports plenty of free space"""
    op = dal.FileStorageOperator(str(tmp_path))
    fake_usage = Mock(total=10**12, used=0, free=10**12)
    with patch(
        "soliplex.ingester.lib.dal.shutil.disk_usage",
        return_value=fake_usage,
    ) as mock_du:
        await op.write("disk_ok_hash", b"payload")
    mock_du.assert_called_once_with(op.store_path)
    assert await op.read("disk_ok_hash") == b"payload"


@pytest.mark.asyncio
async def test_file_storage_operator_write_disk_space_insufficient(tmp_path):
    """write raises OSError(ENOSPC) and creates no file when free space is below threshold"""
    op = dal.FileStorageOperator(str(tmp_path))
    fake_usage = Mock(total=10**12, used=10**12, free=100)
    data = b"payload"
    with patch(
        "soliplex.ingester.lib.dal.shutil.disk_usage",
        return_value=fake_usage,
    ):
        with pytest.raises(OSError, match="insufficient disk space") as exc_info:
            await op.write("nospace_hash", data)
    assert exc_info.value.errno == errno.ENOSPC
    norm_path = op._get_normalized_path("nospace_hash")
    assert not norm_path.exists()


@pytest.mark.asyncio
async def test_file_storage_operator_write_disk_space_exact_data_size_fails(tmp_path):
    """Reserve is enforced: free == len(data) is not enough"""
    op = dal.FileStorageOperator(str(tmp_path))
    data = b"payload"
    fake_usage = Mock(total=10**12, used=10**12, free=len(data))
    with patch(
        "soliplex.ingester.lib.dal.shutil.disk_usage",
        return_value=fake_usage,
    ):
        with pytest.raises(OSError, match="insufficient disk space") as exc_info:
            await op.write("reserve_hash", data)
    assert exc_info.value.errno == errno.ENOSPC


@pytest.mark.asyncio
async def test_file_storage_operator_write_cleans_up_partial_file_on_race(tmp_path):
    """If a race causes write to fail mid-stream, the partial file is unlinked in finally"""
    op = dal.FileStorageOperator(str(tmp_path))
    fake_usage = Mock(total=10**12, used=0, free=10**12)
    norm_path = op._get_normalized_path("race_hash")

    def fake_open(*args, **kwargs):
        # Simulate a partial write: file appears on disk, then writer raises.
        norm_path.write_bytes(b"partial")
        raise OSError(errno.ENOSPC, "no space left on device")

    with patch(
        "soliplex.ingester.lib.dal.shutil.disk_usage",
        return_value=fake_usage,
    ):
        with patch(
            "soliplex.ingester.lib.dal.aiofiles.open",
            side_effect=fake_open,
        ):
            with pytest.raises(OSError, match="no space left") as exc_info:
                await op.write("race_hash", b"data")
    assert exc_info.value.errno == errno.ENOSPC
    assert not norm_path.exists()


@pytest.mark.asyncio
async def test_file_storage_operator_write_cleanup_handles_missing_file(tmp_path):
    """Cleanup swallows FileNotFoundError when no partial file was created"""
    op = dal.FileStorageOperator(str(tmp_path))
    fake_usage = Mock(total=10**12, used=0, free=10**12)
    norm_path = op._get_normalized_path("absent_hash")

    def fake_open(*args, **kwargs):
        # Raise before any file is created on disk.
        raise OSError(errno.EIO, "i/o error")

    with patch(
        "soliplex.ingester.lib.dal.shutil.disk_usage",
        return_value=fake_usage,
    ):
        with patch(
            "soliplex.ingester.lib.dal.aiofiles.open",
            side_effect=fake_open,
        ):
            with pytest.raises(OSError, match="i/o error") as exc_info:
                await op.write("absent_hash", b"data")
    assert exc_info.value.errno == errno.EIO
    assert not norm_path.exists()


@pytest.mark.asyncio
async def test_file_storage_operator_write_protected_no_sidecar_on_disk_full(tmp_path):
    """ProtectedStorageOperator(HASH) does not write a sidecar when primary write fails on disk check"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.HASH)
    fake_usage = Mock(total=10**12, used=10**12, free=100)
    with patch(
        "soliplex.ingester.lib.dal.shutil.disk_usage",
        return_value=fake_usage,
    ):
        with pytest.raises(OSError, match="insufficient disk space") as exc_info:
            await op.write("protected_hash", b"data")
    assert exc_info.value.errno == errno.ENOSPC
    assert not inner._get_normalized_path("protected_hash").exists()
    assert not inner._get_normalized_path("protected_hash.hash").exists()


def test_file_storage_operator_compression_off_by_default(tmp_path):
    """Compression is off when not explicitly enabled"""
    op = dal.FileStorageOperator(str(tmp_path))
    assert op.compress is False
    assert op.compress_level == 3


@pytest.mark.asyncio
async def test_file_storage_operator_compress_round_trip(tmp_path):
    """Compressed write produces a .zst file on disk; read decompresses transparently"""
    op = dal.FileStorageOperator(str(tmp_path), compress=True)
    payload = b'{"k": "v"}\n' * 200_000  # ~2 MB, easily compressible
    await op.write("compressed_hash_aa", payload)

    zst_path = op._get_normalized_path("compressed_hash_aa.zst")
    plain_path = op._get_normalized_path("compressed_hash_aa")
    assert zst_path.exists()
    assert not plain_path.exists()
    # zstd magic bytes: 0x28 0xB5 0x2F 0xFD
    on_disk = zst_path.read_bytes()
    assert on_disk[:4] == b"\x28\xb5\x2f\xfd"
    assert len(on_disk) < len(payload) // 2  # JSON should compress well

    assert await op.read("compressed_hash_aa") == payload


@pytest.mark.asyncio
async def test_file_storage_operator_compress_skips_small_payloads(tmp_path):
    """Payloads under COMPRESS_MIN_BYTES are written plain even when compress=True"""
    op = dal.FileStorageOperator(str(tmp_path), compress=True)
    small = b"x" * 100  # well below COMPRESS_MIN_BYTES (1024)
    await op.write("small_hash_bb", small)

    zst_path = op._get_normalized_path("small_hash_bb.zst")
    plain_path = op._get_normalized_path("small_hash_bb")
    assert plain_path.exists()
    assert not zst_path.exists()
    assert await op.read("small_hash_bb") == small


@pytest.mark.asyncio
async def test_file_storage_operator_read_falls_back_to_plain(tmp_path):
    """Pre-existing plain files are readable when compression is enabled (mixed-format support)"""
    plain_op = dal.FileStorageOperator(str(tmp_path))
    payload = b'{"legacy": true}' * 500
    await plain_op.write("legacy_hash_cc", payload)

    compressing_op = dal.FileStorageOperator(str(tmp_path), compress=True)
    assert await compressing_op.read("legacy_hash_cc") == payload


@pytest.mark.asyncio
async def test_file_storage_operator_exists_handles_both_forms(tmp_path):
    """exists() returns True whether the file is .zst or plain"""
    op = dal.FileStorageOperator(str(tmp_path), compress=True)

    plain_op = dal.FileStorageOperator(str(tmp_path))
    await plain_op.write("legacy_hash_dd", b"plain")
    assert await op.exists("legacy_hash_dd")

    payload = b"y" * 4096
    await op.write("compressed_hash_ee", payload)
    assert await op.exists("compressed_hash_ee")

    assert not await op.exists("missing_hash_ff")


@pytest.mark.asyncio
async def test_file_storage_operator_delete_removes_both_forms(tmp_path):
    """delete() removes whichever forms exist; raises FileNotFoundError when neither does"""
    op = dal.FileStorageOperator(str(tmp_path), compress=True)
    payload = b"z" * 4096
    await op.write("dual_hash_gg", payload)
    # Manually write a plain copy to simulate a leftover from a pre-compression write.
    plain_path = op._get_normalized_path("dual_hash_gg")
    plain_path.write_bytes(b"stale plain")

    await op.delete("dual_hash_gg")
    assert not op._get_normalized_path("dual_hash_gg.zst").exists()
    assert not plain_path.exists()

    with pytest.raises(FileNotFoundError):
        await op.delete("never_existed_hh")


@pytest.mark.asyncio
async def test_file_storage_operator_list_dedupes_compressed_and_plain(tmp_path):
    """list() returns logical names with .zst stripped and dedupes against any plain twin"""
    op = dal.FileStorageOperator(str(tmp_path), compress=True)
    await op.write("listme_hash_ii", b"big payload" * 1000)

    # Drop a plain twin in the same shard dir (simulates partial migration state).
    plain_path = op._get_normalized_path("listme_hash_ii")
    plain_path.write_bytes(b"plain twin")

    # And a separate plain-only entry.
    other_op = dal.FileStorageOperator(str(tmp_path))
    await other_op.write("plainonly_hash_jj", b"hi")

    listing = await op.list("")
    assert "listme_hash_ii" in listing
    assert "plainonly_hash_jj" in listing
    assert "listme_hash_ii.zst" not in listing
    assert listing.count("listme_hash_ii") == 1


@pytest.mark.asyncio
async def test_file_storage_operator_get_uri_prefers_zst(tmp_path):
    """get_uri returns the .zst URI when compressed file exists, else plain URI"""
    op = dal.FileStorageOperator(str(tmp_path), compress=True)
    await op.write("uri_hash_kk", b"q" * 4096)
    uri = op.get_uri("uri_hash_kk")
    assert uri.endswith(".zst")

    other = dal.FileStorageOperator(str(tmp_path))
    await other.write("uri_plain_ll", b"plain")
    assert not op.get_uri("uri_plain_ll").endswith(".zst")


@pytest.mark.asyncio
async def test_file_storage_operator_compress_runs_off_event_loop(tmp_path):
    """Compression and decompression are dispatched via asyncio.to_thread"""
    op = dal.FileStorageOperator(str(tmp_path), compress=True)
    payload = b"e" * 4096

    real_to_thread = asyncio.to_thread
    with patch(
        "soliplex.ingester.lib.dal.asyncio.to_thread",
        side_effect=real_to_thread,
    ) as mock_to_thread:
        await op.write("offload_hash_mm", payload)
        assert mock_to_thread.call_count == 1
        assert mock_to_thread.call_args.args[0] == op._write_compressed_sync

        mock_to_thread.reset_mock()
        result = await op.read("offload_hash_mm")
        assert result == payload
        assert mock_to_thread.call_count == 1
        assert mock_to_thread.call_args.args[0] == op._read_compressed_sync


@pytest.mark.asyncio
async def test_file_storage_operator_compress_level_propagates(tmp_path):
    """compress_level is passed through to ZstdCompressor"""
    op = dal.FileStorageOperator(str(tmp_path), compress=True, compress_level=9)

    real_compressor = zstandard.ZstdCompressor
    with patch(
        "soliplex.ingester.lib.dal.zstandard.ZstdCompressor",
        side_effect=real_compressor,
    ) as mock_cls:
        await op.write("level_hash_nn", b"r" * 4096)
    mock_cls.assert_called_once_with(level=9)


@pytest.mark.asyncio
async def test_file_storage_operator_compress_cleans_up_on_failure(tmp_path):
    """If streaming compression fails mid-write, the partial .zst file is cleaned up"""
    op = dal.FileStorageOperator(str(tmp_path), compress=True)
    payload = b"f" * 4096
    target = op._get_normalized_path("race_zst_oo.zst")

    def fake_compress_sync(path, data):
        path.write_bytes(b"\x28\xb5partial")  # leave a bogus partial file
        raise OSError(errno.ENOSPC, "no space left on device")

    with patch.object(op, "_write_compressed_sync", side_effect=fake_compress_sync):
        with pytest.raises(OSError, match="no space left"):
            await op.write("race_zst_oo", payload)
    assert not target.exists()


@pytest.mark.asyncio
async def test_file_storage_operator_compress_with_protected_hash(tmp_path):
    """Protection wraps compression: payload goes to .zst; integrity sidecar stays plain"""
    inner = dal.FileStorageOperator(str(tmp_path), compress=True)
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.HASH)
    payload = b'{"big": true}' * 1000
    await op.write("protected_zst_pp", payload)

    assert inner._get_normalized_path("protected_zst_pp.zst").exists()
    assert not inner._get_normalized_path("protected_zst_pp").exists()
    # Sidecar is below COMPRESS_MIN_BYTES so it's stored plain (not .zst.zst).
    assert inner._get_normalized_path("protected_zst_pp.hash").exists()
    assert not inner._get_normalized_path("protected_zst_pp.hash.zst").exists()

    assert await op.read("protected_zst_pp") == payload


@pytest.mark.asyncio
async def test_file_storage_operator_compress_with_protected_encrypt(tmp_path):
    """Protection(ENCRYPT) over compressed FileStorageOperator round-trips cleanly"""
    inner = dal.FileStorageOperator(str(tmp_path), compress=True)
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.ENCRYPT, secret=TEST_SECRET)
    payload = b'{"secret": "json"}' * 1000

    await op.write("encrypted_zst_qq", payload)
    # ENCRYPT writes <key>.enc through the inner; with compression, that's <key>.enc.zst.
    assert inner._get_normalized_path("encrypted_zst_qq.enc.zst").exists()
    assert await op.read("encrypted_zst_qq") == payload


def test_get_storage_operator_compresses_listed_artifact_type(tmp_path):
    """get_storage_operator turns on compression for artifact types listed in settings"""
    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings_obj = Mock()
        mock_settings_obj.file_store_target = "fs"
        mock_settings_obj.file_store_dir = str(tmp_path)
        mock_settings_obj.document_store_dir = "docs"
        mock_settings_obj.parsed_json_store_dir = "json"
        mock_settings_obj.file_protection_level = ProtectionLevel.NONE
        mock_settings_obj.file_compression_artifacts = ["parsed_json"]
        mock_settings_obj.file_compression_level = 5
        mock_settings.return_value = mock_settings_obj

        step_config = Mock()
        step_config.id = 1
        step_config.step_type = models.WorkflowStepType.PARSE
        op = dal.get_storage_operator(models.ArtifactType.PARSED_JSON, step_config)
        assert isinstance(op, dal.FileStorageOperator)
        assert op.compress is True
        assert op.compress_level == 5


def test_get_storage_operator_skips_compression_for_unlisted_artifact_type(tmp_path):
    """Artifact types not in file_compression_artifacts get an uncompressed FileStorageOperator"""
    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings_obj = Mock()
        mock_settings_obj.file_store_target = "fs"
        mock_settings_obj.file_store_dir = str(tmp_path)
        mock_settings_obj.document_store_dir = "docs"
        mock_settings_obj.parsed_json_store_dir = "json"
        mock_settings_obj.embeddings_store_dir = "embeddings"
        mock_settings_obj.file_protection_level = ProtectionLevel.NONE
        mock_settings_obj.file_compression_artifacts = ["parsed_json"]
        mock_settings_obj.file_compression_level = 3
        mock_settings.return_value = mock_settings_obj

        step_config = Mock()
        step_config.id = 1
        step_config.step_type = models.WorkflowStepType.EMBED
        op = dal.get_storage_operator(models.ArtifactType.EMBEDDINGS, step_config)
        assert isinstance(op, dal.FileStorageOperator)
        assert op.compress is False


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
        mock_settings_obj.file_protection_level = ProtectionLevel.NONE
        mock_settings_obj.file_compression_artifacts = []
        mock_settings_obj.file_compression_level = 3
        mock_settings.return_value = mock_settings_obj

        op = dal.get_storage_operator(models.ArtifactType.DOC)
        assert isinstance(op, dal.FileStorageOperator)
        assert op.compress is False


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


# --- ProtectedStorageOperator tests ---

TEST_SECRET = "test-secret-key-for-unit-tests"


@pytest.mark.asyncio
async def test_protected_operator_none(tmp_path):
    """NONE mode passes through unchanged"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.NONE)

    await op.write("test_hash_ab", b"hello world")
    result = await op.read("test_hash_ab")
    assert result == b"hello world"


@pytest.mark.asyncio
async def test_protected_operator_hash_write_read(tmp_path):
    """HASH mode writes data + .hash sidecar, read verifies"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.HASH)

    await op.write("test_hash_ab", b"hello world")

    # Sidecar should exist
    assert await inner.exists("test_hash_ab.hash")

    # Read should succeed and return original data
    result = await op.read("test_hash_ab")
    assert result == b"hello world"


@pytest.mark.asyncio
async def test_protected_operator_hash_integrity_error(tmp_path):
    """Corrupted data raises IntegrityError in HASH mode"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.HASH)

    await op.write("test_hash_ab", b"hello world")

    # Corrupt the data file directly
    await inner.write("test_hash_ab", b"corrupted data")

    with pytest.raises(dal.IntegrityError, match="SHA-512 hash mismatch"):
        await op.read("test_hash_ab")


@pytest.mark.asyncio
async def test_protected_operator_hmac_write_read(tmp_path):
    """HMAC mode writes data + .hmac sidecar, read verifies"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.HMAC, secret=TEST_SECRET)

    await op.write("test_hash_ab", b"hello world")

    # Sidecar should exist
    assert await inner.exists("test_hash_ab.hmac")

    # Read should succeed
    result = await op.read("test_hash_ab")
    assert result == b"hello world"


@pytest.mark.asyncio
async def test_protected_operator_hmac_integrity_error(tmp_path):
    """Tampered data raises IntegrityError in HMAC mode"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.HMAC, secret=TEST_SECRET)

    await op.write("test_hash_ab", b"hello world")

    # Tamper with the data
    await inner.write("test_hash_ab", b"tampered data")

    with pytest.raises(dal.IntegrityError, match="HMAC-SHA-512 verification failed"):
        await op.read("test_hash_ab")


@pytest.mark.asyncio
async def test_protected_operator_hmac_wrong_key(tmp_path):
    """Different HMAC key fails verification"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op1 = dal.ProtectedStorageOperator(inner, ProtectionLevel.HMAC, secret="key-one")
    op2 = dal.ProtectedStorageOperator(inner, ProtectionLevel.HMAC, secret="key-two")

    await op1.write("test_hash_ab", b"hello world")

    with pytest.raises(dal.IntegrityError, match="HMAC-SHA-512 verification failed"):
        await op2.read("test_hash_ab")


@pytest.mark.asyncio
async def test_protected_operator_encrypt_write_read(tmp_path):
    """ENCRYPT mode round-trips correctly"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.ENCRYPT, secret=TEST_SECRET)

    await op.write("test_hash_ab", b"hello world")

    result = await op.read("test_hash_ab")
    assert result == b"hello world"


@pytest.mark.asyncio
async def test_protected_operator_encrypt_stores_as_enc(tmp_path):
    """ENCRYPT mode stores ciphertext with .enc extension"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.ENCRYPT, secret=TEST_SECRET)

    plaintext = b"hello world"
    await op.write("test_hash_ab", plaintext)

    # .enc file should exist, plain file should not
    assert await inner.exists("test_hash_ab.enc")
    assert not await inner.exists("test_hash_ab")

    # Raw ciphertext differs from plaintext
    raw = await inner.read("test_hash_ab.enc")
    assert raw != plaintext


@pytest.mark.asyncio
async def test_protected_operator_encrypt_wrong_key(tmp_path):
    """Different encryption key fails to decrypt"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op1 = dal.ProtectedStorageOperator(inner, ProtectionLevel.ENCRYPT, secret="key-one")
    op2 = dal.ProtectedStorageOperator(inner, ProtectionLevel.ENCRYPT, secret="key-two")

    await op1.write("test_hash_ab", b"hello world")

    with pytest.raises(InvalidToken):
        await op2.read("test_hash_ab")


@pytest.mark.asyncio
async def test_protected_operator_delete_removes_hash_sidecar(tmp_path):
    """Delete cleans up .hash sidecar file"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.HASH)

    await op.write("test_hash_ab", b"hello world")
    assert await inner.exists("test_hash_ab.hash")

    await op.delete("test_hash_ab")
    assert not await inner.exists("test_hash_ab")
    assert not await inner.exists("test_hash_ab.hash")


@pytest.mark.asyncio
async def test_protected_operator_delete_removes_hmac_sidecar(tmp_path):
    """Delete cleans up .hmac sidecar file"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.HMAC, secret=TEST_SECRET)

    await op.write("test_hash_ab", b"hello world")
    assert await inner.exists("test_hash_ab.hmac")

    await op.delete("test_hash_ab")
    assert not await inner.exists("test_hash_ab")
    assert not await inner.exists("test_hash_ab.hmac")


@pytest.mark.asyncio
async def test_protected_operator_list_filters_sidecars(tmp_path):
    """List excludes .hash and .hmac entries"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.HASH)

    await op.write("test_hash_ab", b"hello world")

    entries = await op.list("")
    assert "test_hash_ab" in entries
    assert all(not e.endswith(".hash") and not e.endswith(".hmac") for e in entries)


def test_protected_operator_requires_secret():
    """HMAC and ENCRYPT without secret raises ValueError"""
    inner = Mock()
    with pytest.raises(ValueError, match="secret required"):
        dal.ProtectedStorageOperator(inner, ProtectionLevel.HMAC)
    with pytest.raises(ValueError, match="secret required"):
        dal.ProtectedStorageOperator(inner, ProtectionLevel.ENCRYPT)


def test_get_storage_operator_fs_with_protection(tmp_path):
    """get_storage_operator wraps FileStorageOperator with protection"""
    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings_obj = Mock()
        mock_settings_obj.file_store_target = "fs"
        mock_settings_obj.file_store_dir = str(tmp_path)
        mock_settings_obj.document_store_dir = "docs"
        mock_settings_obj.file_protection_level = ProtectionLevel.HASH
        mock_settings_obj.file_secret = None
        mock_settings_obj.file_compression_artifacts = []
        mock_settings_obj.file_compression_level = 3
        mock_settings.return_value = mock_settings_obj

        op = dal.get_storage_operator(models.ArtifactType.DOC)
        assert isinstance(op, dal.ProtectedStorageOperator)


@pytest.mark.asyncio
async def test_protected_operator_exists_delegates(tmp_path):
    """exists delegates to inner operator"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.ENCRYPT, secret=TEST_SECRET)

    assert not await op.exists("test_hash_ab")
    await op.write("test_hash_ab", b"hello")
    assert await op.exists("test_hash_ab")


@pytest.mark.asyncio
async def test_protected_operator_encrypt_delete(tmp_path):
    """Delete removes .enc file in ENCRYPT mode"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.ENCRYPT, secret=TEST_SECRET)

    await op.write("test_hash_ab", b"hello world")
    assert await inner.exists("test_hash_ab.enc")

    await op.delete("test_hash_ab")
    assert not await inner.exists("test_hash_ab.enc")


@pytest.mark.asyncio
async def test_protected_operator_encrypt_list_strips_enc(tmp_path):
    """List returns logical names without .enc extension in ENCRYPT mode"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.ENCRYPT, secret=TEST_SECRET)

    await op.write("test_hash_ab", b"hello world")
    entries = await op.list("")
    assert "test_hash_ab" in entries
    assert not any(e.endswith(".enc") for e in entries)


def test_protected_operator_get_uri_delegates(tmp_path):
    """get_uri delegates to inner operator"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.HASH)

    uri = op.get_uri("test_hash_ab")
    assert uri == inner.get_uri("test_hash_ab")


def test_protected_operator_get_uri_encrypt(tmp_path):
    """get_uri returns .enc path in ENCRYPT mode"""
    inner = dal.FileStorageOperator(str(tmp_path))
    op = dal.ProtectedStorageOperator(inner, ProtectionLevel.ENCRYPT, secret=TEST_SECRET)

    uri = op.get_uri("test_hash_ab")
    assert uri == inner.get_uri("test_hash_ab.enc")


# --- apply_file_protection tests ---


def _make_file_store(tmp_path, store_dir="raw"):
    """Create a minimal file store structure for testing."""
    store = tmp_path / "file_store" / store_dir
    store.mkdir(parents=True)
    return store


def _write_sharded(store, name, data):
    """Write a file into the correct shard directory."""
    stem = name.split(".")[0] if "." in name else name
    shard = stem[-2:]
    shard_dir = store / shard
    shard_dir.mkdir(parents=True, exist_ok=True)
    (shard_dir / name).write_bytes(data)


def _exists_sharded(store, name):
    stem = name.split(".")[0] if "." in name else name
    shard = stem[-2:]
    return (store / shard / name).exists()


def _read_sharded(store, name):
    stem = name.split(".")[0] if "." in name else name
    shard = stem[-2:]
    return (store / shard / name).read_bytes()


@pytest.mark.asyncio
async def test_apply_protection_none_to_hash(tmp_path):
    """Migrate from NONE to HASH adds sidecar files"""
    store = _make_file_store(tmp_path)
    _write_sharded(store, "sha256-aabb", b"hello")

    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings.return_value = Mock(
            file_store_target="fs",
            file_store_dir=str(tmp_path / "file_store"),
            document_store_dir="raw",
            parsed_markdown_store_dir="markdown",
            parsed_json_store_dir="json",
            chunks_store_dir="chunks",
            embeddings_store_dir="embeddings",
        )
        stats = await dal.apply_file_protection(ProtectionLevel.HASH)

    assert stats["processed"] == 1
    assert stats["errors"] == 0
    assert _exists_sharded(store, "sha256-aabb")
    assert _exists_sharded(store, "sha256-aabb.hash")


@pytest.mark.asyncio
async def test_apply_protection_none_to_encrypt(tmp_path):
    """Migrate from NONE to ENCRYPT encrypts and removes plain file"""
    store = _make_file_store(tmp_path)
    _write_sharded(store, "sha256-aabb", b"hello")

    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings.return_value = Mock(
            file_store_target="fs",
            file_store_dir=str(tmp_path / "file_store"),
            document_store_dir="raw",
            parsed_markdown_store_dir="markdown",
            parsed_json_store_dir="json",
            chunks_store_dir="chunks",
            embeddings_store_dir="embeddings",
        )
        stats = await dal.apply_file_protection(ProtectionLevel.ENCRYPT, secret=TEST_SECRET)

    assert stats["processed"] == 1
    assert _exists_sharded(store, "sha256-aabb.enc")
    assert not _exists_sharded(store, "sha256-aabb")


@pytest.mark.asyncio
async def test_apply_protection_encrypt_to_none(tmp_path):
    """Migrate from ENCRYPT to NONE decrypts and removes .enc file"""
    store = _make_file_store(tmp_path)

    # Encrypt the file first
    from cryptography.fernet import Fernet

    master = TEST_SECRET.encode("utf-8")
    fernet_raw = dal._derive_key(master, b"fernet-v1", 32)
    fernet = Fernet(base64.urlsafe_b64encode(fernet_raw))
    _write_sharded(store, "sha256-aabb.enc", fernet.encrypt(b"hello"))

    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings.return_value = Mock(
            file_store_target="fs",
            file_store_dir=str(tmp_path / "file_store"),
            document_store_dir="raw",
            parsed_markdown_store_dir="markdown",
            parsed_json_store_dir="json",
            chunks_store_dir="chunks",
            embeddings_store_dir="embeddings",
        )
        stats = await dal.apply_file_protection(ProtectionLevel.NONE, secret=TEST_SECRET)

    assert stats["processed"] == 1
    assert _exists_sharded(store, "sha256-aabb")
    assert not _exists_sharded(store, "sha256-aabb.enc")
    assert _read_sharded(store, "sha256-aabb") == b"hello"


@pytest.mark.asyncio
async def test_apply_protection_hash_to_none(tmp_path):
    """Migrate from HASH to NONE removes hash sidecars"""
    store = _make_file_store(tmp_path)
    _write_sharded(store, "sha256-aabb", b"hello")
    _write_sharded(store, "sha256-aabb.hash", b"fakehash")

    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings.return_value = Mock(
            file_store_target="fs",
            file_store_dir=str(tmp_path / "file_store"),
            document_store_dir="raw",
            parsed_markdown_store_dir="markdown",
            parsed_json_store_dir="json",
            chunks_store_dir="chunks",
            embeddings_store_dir="embeddings",
        )
        stats = await dal.apply_file_protection(ProtectionLevel.NONE)

    assert stats["processed"] == 1
    assert _exists_sharded(store, "sha256-aabb")
    assert not _exists_sharded(store, "sha256-aabb.hash")


@pytest.mark.asyncio
async def test_apply_protection_requires_fs_target():
    """apply_file_protection rejects non-fs targets"""
    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings.return_value = Mock(file_store_target="db")
        with pytest.raises(ValueError, match="filesystem storage"):
            await dal.apply_file_protection(ProtectionLevel.HASH)


@pytest.mark.asyncio
async def test_apply_protection_requires_secret_for_encrypt():
    """apply_file_protection requires secret for ENCRYPT"""
    with patch("soliplex.ingester.lib.dal.get_settings") as mock_settings:
        mock_settings.return_value = Mock(file_store_target="fs")
        with pytest.raises(ValueError, match="secret required"):
            await dal.apply_file_protection(ProtectionLevel.ENCRYPT)
