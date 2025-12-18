import logging
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from common import do_monkeypatch
from common import mock_engine  # noqa

import soliplex.ingester.lib.models as models
import soliplex.ingester.lib.operations as operations

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_new_document(monkeypatch, mock_engine):  # noqa F811
    do_monkeypatch(monkeypatch, mock_engine)
    test_uri = "/tmp/test.pdf"
    mime_type = "application/pdf"
    test_bytes = b"test bytes"
    test_source = "pytest"
    test_doc_meta = {"test": "test"}

    uri1, doc1 = await operations.create_document_from_uri(
        test_uri, test_source, mime_type, test_bytes, doc_meta=test_doc_meta
    )
    assert doc1 is not None

    assert doc1.doc_meta is not None
    assert uri1 is not None
    assert uri1.id is not None
    assert uri1.uri == test_uri
    assert uri1.source == test_source
    assert uri1.doc_hash == doc1.hash


@pytest.mark.asyncio
async def test_update_document(monkeypatch, mock_engine):  # noqa F811
    """
    tests updating a document with the same uri
    """
    do_monkeypatch(monkeypatch, mock_engine)
    test_uri = "/tmp/test.pdf"
    mime_type = "application/pdf"
    test_bytes = b"test bytes"
    test_source = "pytest"
    test_doc_meta = {"test": "test"}

    uri1, doc1 = await operations.create_document_from_uri(
        test_uri, test_source, mime_type, test_bytes, doc_meta=test_doc_meta
    )
    assert uri1.version == 1
    assert doc1 is not None
    assert doc1.hash is not None
    assert uri1.doc_hash == doc1.hash
    test_bytes2 = b"test bytes2"
    # this should be the same uri
    uri2, doc2 = await operations.create_document_from_uri(
        test_uri,
        test_source,
        None,
        test_bytes2,
        doc_meta=test_doc_meta,
    )
    assert uri1.id == uri2.id
    assert uri1.doc_hash != uri2.doc_hash
    assert uri2.doc_hash == doc2.hash
    assert uri2.version == 2

    # create a doc with a different source
    # and it should be considered different
    test_source2 = "pytest2"
    uri3, doc3 = await operations.create_document_from_uri(
        test_uri,
        test_source2,
        mime_type,
        test_bytes2,
        doc_meta=test_doc_meta,
    )
    assert uri3.id != uri1.id
    assert uri3.doc_hash == uri2.doc_hash
    assert uri3.doc_hash == doc3.hash
    assert doc2.hash == doc3.hash

    # check documne th history
    history = await operations.get_document_uri_history(uri1.id)
    for h in history:
        logger.info(f"h={h}")
    assert len(history) == 2


@pytest.mark.asyncio
async def test_get_uri_for_hash(monkeypatch, mock_engine):  # noqa F811
    do_monkeypatch(monkeypatch, mock_engine)
    test_uri = "/tmp/test.pdf"
    mime_type = "application/pdf"
    test_bytes = b"test bytes"
    test_source = "pytest"
    test_doc_meta = {"test": "test"}

    uri1, doc1 = await operations.create_document_from_uri(
        test_uri, test_source, mime_type, test_bytes, doc_meta=test_doc_meta
    )
    test_uri2 = "/tmp/test2.pdf"
    uri2, doc2 = await operations.create_document_from_uri(
        test_uri2, test_source, mime_type, test_bytes, doc_meta=test_doc_meta
    )

    source_files = await operations.get_document_uris_by_hash(doc1.hash)
    for source_file in source_files:
        assert source_file.source == test_source
    assert len(source_files) == 2

    find1 = await operations.find_document_uri(test_uri, test_source)
    assert find1 is not None
    assert find1.uri == test_uri
    test_uri_missing = "/tmp/missing.pdf"
    find2 = await operations.find_document_uri(test_uri_missing, test_source)
    assert find2 is None


@pytest.mark.asyncio
async def test_status(monkeypatch, mock_engine):  # noqa F811
    do_monkeypatch(monkeypatch, mock_engine)
    test_uri = "/tmp/test.pdf"
    mime_type = "application/pdf"
    test_bytes = b"test bytes"
    test_source = "pytest"
    test_doc_meta = {"test": "test"}

    uri1, doc1 = await operations.create_document_from_uri(
        test_uri, test_source, mime_type, test_bytes, doc_meta=test_doc_meta
    )

    test_uri2 = "/tmp/test2.pdf"
    test_bytes2 = b"test bytes2"
    uri2, doc2 = await operations.create_document_from_uri(
        test_uri2, test_source, mime_type, test_bytes2, doc_meta=test_doc_meta
    )
    missing_uri = "/temp/missing.pdf"
    # base case 2 existing docs and 1 new
    hashes = {
        test_uri: doc1.hash,
        test_uri2: doc2.hash,
        missing_uri: "test_hash",
    }

    status, to_delete = await operations.get_doc_status(test_source, hashes)
    logger.info(f"status={status}")
    logger.info(f"to_delete={to_delete}")
    assert status is not None
    assert len(status) == len(hashes)
    assert status[test_uri]["status"] == "matched"
    assert status[test_uri2]["status"] == "matched"
    assert status[missing_uri]["status"] == "new"
    assert len(to_delete) == 0

    # 1 updated doc, 1 existing
    hashes2 = {
        test_uri: "mismatched_hash",
        test_uri2: doc2.hash,
    }

    status2, to_delete2 = await operations.get_doc_status(test_source, hashes2)
    assert status2[test_uri]["status"] == "mismatch"
    assert status2[test_uri2]["status"] == "matched"
    assert len(to_delete2) == 0

    # 1 doc missing
    hashes3 = {
        test_uri: doc1.hash,
    }
    status3, to_delete3 = await operations.get_doc_status(test_source, hashes3)
    assert len(status3) == 1
    assert len(to_delete3) == 1

    status4 = await operations.update_doc_status(test_source, hashes3)
    logger.info(f"status4={status4}")


@pytest.mark.asyncio
async def test_history_for_hash(monkeypatch, mock_engine):  # noqa F811
    do_monkeypatch(monkeypatch, mock_engine)

    test_uri = "/tmp/test.pdf"
    mime_type = "application/pdf"
    test_bytes = b"test bytes"
    test_source = "pytest"
    test_doc_meta = {"test": "test"}

    uri1, doc1 = await operations.create_document_from_uri(
        test_uri, test_source, mime_type, test_bytes, doc_meta=test_doc_meta
    )
    await operations.add_history_for_hash(doc1.hash, "test history")

    history = await operations.get_document_uri_history(uri1.id)
    assert len(history) == 2

    await operations.add_history_for_hash(doc1.hash, "test history2", hist_meta=None)


@pytest.mark.asyncio
async def test_delete_document(monkeypatch, mock_engine):  # noqa F811
    do_monkeypatch(monkeypatch, mock_engine)
    test_uri = "/tmp/test.pdf"
    mime_type = "application/pdf"
    test_bytes = b"test bytes"
    test_source = "pytest"
    test_doc_meta = {"test": "test"}

    uri1, doc1 = await operations.create_document_from_uri(
        test_uri, test_source, mime_type, test_bytes, doc_meta=test_doc_meta
    )
    # this should fail with foreign key check
    with pytest.raises(Exception, match="has existing"):  # noqa: PT012
        async with models.get_session() as session:
            await operations.delete_document(doc1.hash, session)
            await session.commit()
    # this will succeed
    async with models.get_session() as session:
        await operations.delete_document_uri(uri1.id, session)
        await session.commit()
    check = await operations.get_document_uris_by_hash(doc1.hash)
    assert len(check) == 0


def test_document_not_found_error():
    """Test DocumentNotFoundError exception"""
    error = operations.DocumentNotFoundError("test_hash")
    assert "test_hash" in str(error)


def test_document_invalid_error():
    """Test DocumentInvalidError exception"""
    error = operations.DocumentInvalidError("test_hash")
    assert "test_hash" in str(error)


def test_batch_not_found_error():
    """Test BatchNotFoundError exception"""
    error = operations.BatchNotFoundError(123)
    assert "123" in str(error)


def test_batch_completed_error():
    """Test BatchCompletedError exception"""
    error = operations.BatchCompletedError(123)
    assert "123" in str(error)


def test_guess_mime_type_unknown():
    """Test _guess_mime_type with unknown extension"""
    mime = operations._guess_mime_type("/path/to/file.unknownext")
    assert mime == "application/octet-stream"


def test_guess_extension_unknown():
    """Test guess_extension with unknown mime type"""
    ext = operations.guess_extension("application/x-unknown")
    assert ext == ".bin"


def test_guess_extension_known():
    """Test guess_extension with known mime type"""
    ext = operations.guess_extension("application/pdf")
    assert ext == ".pdf"


@pytest.mark.asyncio
async def test_handle_file_no_input(monkeypatch, mock_engine):  # noqa F811
    """Test handle_file with no input_uri or file_bytes"""
    do_monkeypatch(monkeypatch, mock_engine)
    async with models.get_session() as session:
        with pytest.raises(ValueError, match="input_uri or file_bytes must be provided"):
            await operations.handle_file(session, input_uri=None, file_bytes=None)


@pytest.mark.asyncio
async def test_handle_file_empty_bytes(monkeypatch, mock_engine):  # noqa F811
    """Test handle_file with empty file_bytes"""
    do_monkeypatch(monkeypatch, mock_engine)
    async with models.get_session() as session:
        with pytest.raises(ValueError, match="file_bytes must be provided"):
            await operations.handle_file(session, input_uri=None, file_bytes=b"")


@pytest.mark.asyncio
async def test_handle_file_with_input_uri(monkeypatch, mock_engine):  # noqa F811
    """Test handle_file with input_uri"""
    do_monkeypatch(monkeypatch, mock_engine)
    test_bytes = b"test file content"

    with patch("soliplex.ingester.lib.operations.dal.read_input_url") as mock_read:
        with patch("soliplex.ingester.lib.operations.dal.get_storage_operator") as mock_get_op:
            mock_read.return_value = test_bytes
            mock_op = AsyncMock()
            mock_op.is_exist.return_value = False
            mock_op.write = AsyncMock()
            mock_get_op.return_value = mock_op

            async with models.get_session() as session:
                hash_result, size, md5 = await operations.handle_file(session, input_uri="http://test.com/file.pdf")
                assert size == len(test_bytes)
                assert hash_result.startswith("sha256-")
                mock_read.assert_called_once()
                mock_op.write.assert_called_once()


@pytest.mark.asyncio
async def test_read_doc_bytes(monkeypatch, mock_engine):  # noqa F811
    """Test read_doc_bytes function"""
    do_monkeypatch(monkeypatch, mock_engine)
    test_bytes = b"test content"

    with patch("soliplex.ingester.lib.operations.dal.get_storage_operator") as mock_get_op:
        mock_op = AsyncMock()
        mock_op.read.return_value = test_bytes
        mock_get_op.return_value = mock_op

        result = await operations.read_doc_bytes("test_hash", models.ArtifactType.DOC)
        assert result == test_bytes
        mock_op.read.assert_called_once_with("test_hash")


@pytest.mark.asyncio
async def test_delete_file(monkeypatch, mock_engine):  # noqa F811
    """Test delete_file function"""
    do_monkeypatch(monkeypatch, mock_engine)

    with patch("soliplex.ingester.lib.operations.dal.get_storage_operator") as mock_get_op:
        with patch("soliplex.ingester.lib.operations.add_history_for_hash") as mock_history:
            mock_op = AsyncMock()
            mock_op.delete.side_effect = FileNotFoundError("File not found")
            mock_get_op.return_value = mock_op

            async with models.get_session() as session:
                await operations.delete_file("test_hash", session)
                mock_history.assert_called_once()


@pytest.mark.asyncio
async def test_update_doc_meta(monkeypatch, mock_engine):  # noqa F811
    do_monkeypatch(monkeypatch, mock_engine)
    test_uri = "/tmp/test.pdf"
    mime_type = "application/pdf"
    test_bytes = b"test bytes"
    test_source = "pytest"
    test_doc_meta = {"test": "test"}

    uri1, doc1 = await operations.create_document_from_uri(
        test_uri, test_source, mime_type, test_bytes, doc_meta=test_doc_meta
    )
    assert doc1 is not None

    assert doc1.doc_meta is not None

    test_doc_meta["new_key"] = 1
    await operations.update_doc_meta(doc1.hash, test_doc_meta)
    doc1 = await operations.get_document(doc1.hash)
    assert doc1.doc_meta["new_key"] == 1

    with pytest.raises(operations.DocumentNotFoundError):
        await operations.update_doc_meta("unknown_hash", test_doc_meta)
