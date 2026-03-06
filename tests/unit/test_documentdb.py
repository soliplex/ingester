"""
Unit tests for DocumentDB record creation and deletion.

Tests the DocumentDB tracking of documents stored in HaikuRAG,
including record creation, deletion, and cascading through
delete_document_uri_by_uri.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from sqlmodel import select

import soliplex.ingester.lib.operations as doc_ops
from soliplex.ingester.lib import rag
from soliplex.ingester.lib.models import DocumentDB
from soliplex.ingester.lib.models import StepConfig
from soliplex.ingester.lib.models import get_session


@pytest.fixture
def mock_settings():
    """Create a mock settings object"""
    settings = MagicMock()
    settings.lancedb_dir = "/tmp/lancedb"
    return settings


@pytest.fixture
def mock_step_config():
    """Create a mock step config with data_dir"""
    step_config = MagicMock(spec=StepConfig)
    step_config.config_json = {"data_dir": "test-project"}
    return step_config


@pytest.mark.asyncio
async def test_create_document_db_record(db, mock_settings, mock_step_config):
    """Test that create_document_db_record creates a record with correct fields."""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        record = await rag.create_document_db_record(
            doc_hash="sha256-test123",
            source="test_source",
            step_config=mock_step_config,
            rag_id="haiku-rag-id-456",
            chunk_count=42,
        )

        # Verify returned record has correct values
        assert record.doc_hash == "sha256-test123"
        assert record.source == "test_source"
        assert record.db_name == "test-project"
        assert record.lancedb_dir == "/tmp/lancedb"
        assert record.rag_id == "haiku-rag-id-456"
        assert record.chunk_count == 42
        assert record.created_date is not None
        assert record.id is not None

        # Verify record persisted to database
        async with get_session() as session:
            q = select(DocumentDB).where(DocumentDB.id == record.id)
            result = await session.exec(q)
            db_record = result.first()
            assert db_record is not None
            assert db_record.doc_hash == "sha256-test123"
            assert db_record.rag_id == "haiku-rag-id-456"


@pytest.mark.asyncio
async def test_create_document_db_record_s3_lancedb_dir(db, mock_step_config):
    """Test that create_document_db_record handles S3 lancedb_dir correctly."""
    mock_settings_s3 = MagicMock()
    mock_settings_s3.lancedb_dir = "s3://my-bucket/lancedb"

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings_s3):
        record = await rag.create_document_db_record(
            doc_hash="sha256-s3test",
            source="s3_source",
            step_config=mock_step_config,
            rag_id="s3-rag-id",
            chunk_count=10,
        )

        assert record.lancedb_dir == "s3://my-bucket/lancedb"
        assert record.db_name == "test-project"


@pytest.mark.asyncio
async def test_multiple_documentdb_records_for_same_hash(db, mock_settings):
    """Test that multiple DocumentDB records can exist for the same doc_hash (1:many mapping)."""
    step_config1 = MagicMock(spec=StepConfig)
    step_config1.config_json = {"data_dir": "project-a"}

    step_config2 = MagicMock(spec=StepConfig)
    step_config2.config_json = {"data_dir": "project-b"}

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        # Create two records for the same doc_hash but different data_dirs
        await rag.create_document_db_record(
            doc_hash="sha256-shared",
            source="source_a",
            step_config=step_config1,
            rag_id="rag-id-1",
            chunk_count=10,
        )

        await rag.create_document_db_record(
            doc_hash="sha256-shared",
            source="source_b",
            step_config=step_config2,
            rag_id="rag-id-2",
            chunk_count=20,
        )

        # Verify both records exist
        async with get_session() as session:
            q = select(DocumentDB).where(DocumentDB.doc_hash == "sha256-shared")
            result = await session.exec(q)
            records = result.all()
            assert len(records) == 2
            db_names = {r.db_name for r in records}
            assert db_names == {"project-a", "project-b"}


@pytest.mark.asyncio
async def test_delete_from_rag_by_hash_no_records(db):
    """Test that delete_from_rag_by_hash returns zeros when no records exist."""
    result = await rag.delete_from_rag_by_hash("sha256-nonexistent")

    assert result["deleted_rag_entries"] == 0
    assert result["deleted_documentdb_records"] == 0


@pytest.mark.asyncio
async def test_delete_from_rag_by_hash_single_record(db, mock_settings, mock_step_config):
    """Test deletion of a single DocumentDB record and its HaikuRAG entry."""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        # Create a DocumentDB record
        await rag.create_document_db_record(
            doc_hash="sha256-todelete",
            source="test_source",
            step_config=mock_step_config,
            rag_id="rag-to-delete",
            chunk_count=5,
        )

    # Mock HaikuRAG client for deletion
    mock_client = MagicMock()
    mock_client.delete_document = AsyncMock()

    with (
        patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings),
        patch("soliplex.ingester.lib.rag.HaikuRAG") as mock_haiku_rag,
        patch("soliplex.ingester.lib.rag.build_embed_config"),
        patch("soliplex.ingester.lib.rag.build_storage_config"),
    ):
        mock_haiku_rag.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_haiku_rag.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await rag.delete_from_rag_by_hash("sha256-todelete")

    # Verify HaikuRAG deletion was called
    mock_client.delete_document.assert_called_once_with("rag-to-delete")

    # Verify statistics
    assert result["deleted_rag_entries"] == 1
    assert result["deleted_documentdb_records"] == 1

    # Verify record removed from database
    async with get_session() as session:
        q = select(DocumentDB).where(DocumentDB.doc_hash == "sha256-todelete")
        db_result = await session.exec(q)
        assert db_result.first() is None


@pytest.mark.asyncio
async def test_delete_from_rag_by_hash_multiple_records(db, mock_settings):
    """Test deletion of multiple DocumentDB records for the same hash."""
    step_config1 = MagicMock(spec=StepConfig)
    step_config1.config_json = {"data_dir": "project-a"}

    step_config2 = MagicMock(spec=StepConfig)
    step_config2.config_json = {"data_dir": "project-b"}

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        # Create multiple records
        await rag.create_document_db_record(
            doc_hash="sha256-multi",
            source="source_a",
            step_config=step_config1,
            rag_id="rag-id-a",
            chunk_count=10,
        )
        await rag.create_document_db_record(
            doc_hash="sha256-multi",
            source="source_b",
            step_config=step_config2,
            rag_id="rag-id-b",
            chunk_count=20,
        )

    # Mock HaikuRAG client for deletion
    mock_client = MagicMock()
    mock_client.delete_document = AsyncMock()

    with (
        patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings),
        patch("soliplex.ingester.lib.rag.HaikuRAG") as mock_haiku_rag,
        patch("soliplex.ingester.lib.rag.build_embed_config"),
        patch("soliplex.ingester.lib.rag.build_storage_config"),
    ):
        mock_haiku_rag.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_haiku_rag.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await rag.delete_from_rag_by_hash("sha256-multi")

    # Verify both HaikuRAG deletions were called
    assert mock_client.delete_document.call_count == 2

    # Verify statistics
    assert result["deleted_rag_entries"] == 2
    assert result["deleted_documentdb_records"] == 2

    # Verify all records removed from database
    async with get_session() as session:
        q = select(DocumentDB).where(DocumentDB.doc_hash == "sha256-multi")
        db_result = await session.exec(q)
        assert len(db_result.all()) == 0


@pytest.mark.asyncio
async def test_delete_from_rag_by_hash_rag_failure_still_deletes_db_records(db, mock_settings, mock_step_config):
    """Test that DocumentDB records are deleted even if HaikuRAG deletion fails."""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        await rag.create_document_db_record(
            doc_hash="sha256-ragfail",
            source="test_source",
            step_config=mock_step_config,
            rag_id="rag-will-fail",
            chunk_count=5,
        )

    # Mock HaikuRAG client to raise an exception
    mock_client = MagicMock()
    mock_client.delete_document = AsyncMock(side_effect=Exception("Connection failed"))

    with (
        patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings),
        patch("soliplex.ingester.lib.rag.HaikuRAG") as mock_haiku_rag,
        patch("soliplex.ingester.lib.rag.build_embed_config"),
        patch("soliplex.ingester.lib.rag.build_storage_config"),
    ):
        mock_haiku_rag.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_haiku_rag.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await rag.delete_from_rag_by_hash("sha256-ragfail")

    # RAG deletion failed, so count is 0
    assert result["deleted_rag_entries"] == 0
    # But DocumentDB record should still be deleted
    assert result["deleted_documentdb_records"] == 1

    # Verify record removed from database
    async with get_session() as session:
        q = select(DocumentDB).where(DocumentDB.doc_hash == "sha256-ragfail")
        db_result = await session.exec(q)
        assert db_result.first() is None


@pytest.mark.asyncio
async def test_delete_from_rag_by_hash_no_rag_id(db, mock_settings, mock_step_config):
    """Test deletion of a DocumentDB record with no rag_id (skip RAG deletion)."""
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        # Create a record without rag_id
        async with get_session() as session:
            import datetime

            record = DocumentDB(
                doc_hash="sha256-noragid",
                source="test_source",
                db_name="test-project",
                lancedb_dir="/tmp/lancedb",
                rag_id=None,  # No rag_id
                chunk_count=5,
                created_date=datetime.datetime.now(datetime.UTC),
            )
            session.add(record)
            await session.commit()

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        result = await rag.delete_from_rag_by_hash("sha256-noragid")

    # No RAG deletion since no rag_id
    assert result["deleted_rag_entries"] == 0
    # But DocumentDB record should be deleted
    assert result["deleted_documentdb_records"] == 1


@pytest.mark.asyncio
async def test_delete_document_uri_includes_rag_deletion(db, mock_settings, mock_step_config):
    """Test that delete_document_uri_by_uri includes RAG deletion in statistics."""
    # Create test document
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/rag_delete_test.pdf"
    test_bytes = b"test bytes for rag deletion"
    doc_uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    # Create a DocumentDB record for this document
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        await rag.create_document_db_record(
            doc_hash=doc.hash,
            source="test_source",
            step_config=mock_step_config,
            rag_id="test-rag-id",
            chunk_count=10,
        )

    # Mock HaikuRAG client for deletion
    mock_client = MagicMock()
    mock_client.delete_document = AsyncMock()

    with (
        patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings),
        patch("soliplex.ingester.lib.rag.HaikuRAG") as mock_haiku_rag,
        patch("soliplex.ingester.lib.rag.build_embed_config"),
        patch("soliplex.ingester.lib.rag.build_storage_config"),
    ):
        mock_haiku_rag.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_haiku_rag.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await doc_ops.delete_document_uri_by_uri(test_uri, "test_source")

    # Verify RAG deletion statistics are included
    assert "deleted_rag_entries" in result
    assert "deleted_documentdb_records" in result
    assert result["deleted_rag_entries"] == 1
    assert result["deleted_documentdb_records"] == 1

    # Verify RAG deletion was called
    mock_client.delete_document.assert_called_once_with("test-rag-id")

    # Verify total includes RAG counts
    expected_total = (
        result["deleted_document_uris"]
        + result["deleted_uri_history"]
        + result["deleted_documents"]
        + result["deleted_workflow_runs"]
        + result["deleted_run_steps"]
        + result["deleted_lifecycle_history"]
        + result["deleted_rag_entries"]
        + result["deleted_documentdb_records"]
    )
    assert result["total_deleted"] == expected_total


@pytest.mark.asyncio
async def test_delete_document_uri_multiple_references_no_rag_deletion(db, mock_settings, mock_step_config):
    """Test that RAG entries are NOT deleted when multiple URIs reference the document."""
    # Create test data with two URIs pointing to the same document
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_bytes = b"shared document bytes"

    # Create first URI
    test_uri1 = "/tmp/multi_rag_test1.pdf"
    doc_uri1, doc = await doc_ops.create_document_from_uri(
        test_uri1, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    # Create second URI pointing to the same document (same bytes = same hash)
    test_uri2 = "/tmp/multi_rag_test2.pdf"
    doc_uri2, doc2 = await doc_ops.create_document_from_uri(
        test_uri2, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    # Create a DocumentDB record
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        await rag.create_document_db_record(
            doc_hash=doc.hash,
            source="test_source",
            step_config=mock_step_config,
            rag_id="shared-rag-id",
            chunk_count=10,
        )

    # Delete only the first URI
    result = await doc_ops.delete_document_uri_by_uri(test_uri1, "test_source")

    # RAG entries should NOT be deleted since document still has another reference
    assert result["deleted_rag_entries"] == 0
    assert result["deleted_documentdb_records"] == 0
    assert result["deleted_documents"] == 0

    # Verify DocumentDB record still exists
    async with get_session() as session:
        q = select(DocumentDB).where(DocumentDB.doc_hash == doc.hash)
        db_result = await session.exec(q)
        assert db_result.first() is not None


@pytest.mark.asyncio
async def test_delete_orphaned_documents_includes_rag_deletion(db, mock_settings, mock_step_config):
    """Test that delete_orphaned_documents includes RAG deletion in statistics."""
    # Create a document without a URI (orphaned)
    # We need to directly insert a Document record
    from soliplex.ingester.lib.models import Document

    doc_hash = "sha256-orphaned-test"
    async with get_session() as session:
        doc = Document(hash=doc_hash, mime_type="application/pdf", file_size=100)
        session.add(doc)
        await session.commit()

    # Create a DocumentDB record for this orphaned document
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        await rag.create_document_db_record(
            doc_hash=doc_hash,
            source="test_source",
            step_config=mock_step_config,
            rag_id="orphaned-rag-id",
            chunk_count=5,
        )

    # Mock HaikuRAG client for deletion
    mock_client = MagicMock()
    mock_client.delete_document = AsyncMock()

    with (
        patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings),
        patch("soliplex.ingester.lib.rag.HaikuRAG") as mock_haiku_rag,
        patch("soliplex.ingester.lib.rag.build_embed_config"),
        patch("soliplex.ingester.lib.rag.build_storage_config"),
    ):
        mock_haiku_rag.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_haiku_rag.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await doc_ops.delete_orphaned_documents()

    # Verify RAG deletion statistics are included
    assert "deleted_rag_entries" in result
    assert "deleted_documentdb_records" in result
    assert result["deleted_rag_entries"] == 1
    assert result["deleted_documentdb_records"] == 1
    assert result["deleted_documents"] == 1

    # Verify RAG deletion was called
    mock_client.delete_document.assert_called_once_with("orphaned-rag-id")


@pytest.mark.asyncio
async def test_list_documentdb_databases_empty(db):
    """Test list_documentdb_databases returns empty list when no records exist."""
    result = await doc_ops.list_documentdb_databases()
    assert result == []


@pytest.mark.asyncio
async def test_list_documentdb_databases_with_records(db, mock_settings):
    """Test list_documentdb_databases returns correct counts per database."""
    step_config1 = MagicMock(spec=StepConfig)
    step_config1.config_json = {"data_dir": "project-a"}

    step_config2 = MagicMock(spec=StepConfig)
    step_config2.config_json = {"data_dir": "project-b"}

    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        # Create 2 documents in project-a
        await rag.create_document_db_record(
            doc_hash="sha256-doc1",
            source="source1",
            step_config=step_config1,
            rag_id="rag-1",
            chunk_count=10,
        )
        await rag.create_document_db_record(
            doc_hash="sha256-doc2",
            source="source2",
            step_config=step_config1,
            rag_id="rag-2",
            chunk_count=20,
        )

        # Create 1 document in project-b
        await rag.create_document_db_record(
            doc_hash="sha256-doc3",
            source="source3",
            step_config=step_config2,
            rag_id="rag-3",
            chunk_count=15,
        )

    result = await doc_ops.list_documentdb_databases()

    assert len(result) == 2

    # Find project-a
    project_a = next((r for r in result if r["db_name"] == "project-a"), None)
    assert project_a is not None
    assert project_a["lancedb_dir"] == "/tmp/lancedb"
    assert project_a["document_count"] == 2
    assert project_a["total_chunks"] == 30

    # Find project-b
    project_b = next((r for r in result if r["db_name"] == "project-b"), None)
    assert project_b is not None
    assert project_b["lancedb_dir"] == "/tmp/lancedb"
    assert project_b["document_count"] == 1
    assert project_b["total_chunks"] == 15


@pytest.mark.asyncio
async def test_list_documents_in_rag_db_empty(db, mock_settings):
    """Test list_documents_in_rag_db returns empty list when no matching records."""
    with patch("soliplex.ingester.lib.operations.get_settings", return_value=mock_settings):
        result = await doc_ops.list_documents_in_rag_db("nonexistent-db")
    assert result == []


@pytest.mark.asyncio
async def test_list_documents_in_rag_db_with_records(db, mock_settings):
    """Test list_documents_in_rag_db returns correct document information."""
    from soliplex.ingester.lib.models import Document
    from soliplex.ingester.lib.models import DocumentURI

    step_config = MagicMock(spec=StepConfig)
    step_config.config_json = {"data_dir": "test-project"}

    # Create Document and DocumentURI records
    async with get_session() as session:
        doc = Document(hash="sha256-listdocs1", mime_type="application/pdf", file_size=12345)
        session.add(doc)
        await session.flush()

        doc_uri = DocumentURI(
            doc_hash="sha256-listdocs1",
            uri="/path/to/doc1.pdf",
            source="test_source",
            version=1,
        )
        session.add(doc_uri)

    # Create DocumentDB record
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        await rag.create_document_db_record(
            doc_hash="sha256-listdocs1",
            source="test_source",
            step_config=step_config,
            rag_id="rag-listdocs-1",
            chunk_count=25,
        )

    with patch("soliplex.ingester.lib.operations.get_settings", return_value=mock_settings):
        result = await doc_ops.list_documents_in_rag_db("test-project")

    assert len(result) == 1
    doc_info = result[0]
    assert doc_info["doc_hash"] == "sha256-listdocs1"
    assert doc_info["rag_id"] == "rag-listdocs-1"
    assert doc_info["chunk_count"] == 25
    assert doc_info["source"] == "test_source"
    assert doc_info["uri"] == "/path/to/doc1.pdf"
    assert doc_info["mime_type"] == "application/pdf"
    assert doc_info["file_size"] == 12345
    assert doc_info["created_date"] is not None


@pytest.mark.asyncio
async def test_list_documents_in_rag_db_filters_by_lancedb_dir(db, mock_settings):
    """Test list_documents_in_rag_db correctly filters by lancedb_dir."""
    step_config = MagicMock(spec=StepConfig)
    step_config.config_json = {"data_dir": "shared-db"}

    # Create record with default lancedb_dir
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        await rag.create_document_db_record(
            doc_hash="sha256-default-dir",
            source="source1",
            step_config=step_config,
            rag_id="rag-default",
            chunk_count=10,
        )

    # Create record with different lancedb_dir
    mock_settings_s3 = MagicMock()
    mock_settings_s3.lancedb_dir = "s3://bucket/lancedb"
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings_s3):
        await rag.create_document_db_record(
            doc_hash="sha256-s3-dir",
            source="source2",
            step_config=step_config,
            rag_id="rag-s3",
            chunk_count=20,
        )

    # Query with default lancedb_dir
    with patch("soliplex.ingester.lib.operations.get_settings", return_value=mock_settings):
        result_default = await doc_ops.list_documents_in_rag_db("shared-db")

    assert len(result_default) == 1
    assert result_default[0]["doc_hash"] == "sha256-default-dir"

    # Query with explicit S3 lancedb_dir
    with patch("soliplex.ingester.lib.operations.get_settings", return_value=mock_settings):
        result_s3 = await doc_ops.list_documents_in_rag_db("shared-db", lancedb_dir="s3://bucket/lancedb")

    assert len(result_s3) == 1
    assert result_s3[0]["doc_hash"] == "sha256-s3-dir"


@pytest.mark.asyncio
async def test_check_rag_db_consistency_lancedb_not_accessible(db, mock_settings):
    """Test check_rag_db_consistency handles inaccessible LanceDB gracefully."""
    step_config = MagicMock(spec=StepConfig)
    step_config.config_json = {"data_dir": "test-check-db"}

    # Create a DocumentDB record
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        await rag.create_document_db_record(
            doc_hash="sha256-check1",
            source="test_source",
            step_config=step_config,
            rag_id="rag-check-1",
            chunk_count=10,
        )

    # Mock HaikuRAG to raise an error (database doesn't exist)
    with (
        patch("soliplex.ingester.lib.operations.get_settings", return_value=mock_settings),
        patch("haiku.rag.client.HaikuRAG") as mock_haiku_rag,
        patch("haiku.rag.config.get_config"),
    ):
        mock_haiku_rag.return_value.__aenter__ = AsyncMock(side_effect=Exception("Database not found"))

        result = await doc_ops.check_rag_db_consistency("test-check-db")

    # Should return error but still list DocumentDB records
    assert "error" in result
    assert result["documentdb_count"] == 1
    assert result["lancedb_count"] == 0
    assert len(result["in_documentdb_only"]) == 1
    assert result["in_documentdb_only"][0]["rag_id"] == "rag-check-1"


@pytest.mark.asyncio
async def test_check_rag_db_consistency_all_matched(db, mock_settings):
    """Test check_rag_db_consistency when all documents match."""
    step_config = MagicMock(spec=StepConfig)
    step_config.config_json = {"data_dir": "matched-db"}

    # Create DocumentDB records
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        await rag.create_document_db_record(
            doc_hash="sha256-matched1",
            source="test_source",
            step_config=step_config,
            rag_id="rag-matched-1",
            chunk_count=10,
        )
        await rag.create_document_db_record(
            doc_hash="sha256-matched2",
            source="test_source",
            step_config=step_config,
            rag_id="rag-matched-2",
            chunk_count=20,
        )

    # Mock HaikuRAG to return matching documents
    mock_doc1 = MagicMock()
    mock_doc1.id = "rag-matched-1"
    mock_doc1.uri = "/path/to/doc1.pdf"
    mock_doc1.title = "Document 1"
    mock_doc1.created_at = None
    mock_doc1.chunk_count = 10
    mock_doc1.metadata = {}

    mock_doc2 = MagicMock()
    mock_doc2.id = "rag-matched-2"
    mock_doc2.uri = "/path/to/doc2.pdf"
    mock_doc2.title = "Document 2"
    mock_doc2.created_at = None
    mock_doc2.chunk_count = 20
    mock_doc2.metadata = {}

    mock_client = MagicMock()
    mock_client.list_documents = AsyncMock(return_value=[mock_doc1, mock_doc2])

    with (
        patch("soliplex.ingester.lib.operations.get_settings", return_value=mock_settings),
        patch("haiku.rag.client.HaikuRAG") as mock_haiku_rag,
        patch("haiku.rag.config.get_config"),
    ):
        mock_haiku_rag.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_haiku_rag.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await doc_ops.check_rag_db_consistency("matched-db")

    assert "error" not in result
    assert result["documentdb_count"] == 2
    assert result["lancedb_count"] == 2
    assert result["matched"] == 2
    assert len(result["in_documentdb_only"]) == 0
    assert len(result["in_lancedb_only"]) == 0


@pytest.mark.asyncio
async def test_check_rag_db_consistency_with_discrepancies(db, mock_settings):
    """Test check_rag_db_consistency detects discrepancies in both directions."""
    step_config = MagicMock(spec=StepConfig)
    step_config.config_json = {"data_dir": "discrepancy-db"}

    # Create DocumentDB records
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        # This one will match
        await rag.create_document_db_record(
            doc_hash="sha256-common",
            source="test_source",
            step_config=step_config,
            rag_id="rag-common",
            chunk_count=10,
        )
        # This one is only in DocumentDB
        await rag.create_document_db_record(
            doc_hash="sha256-docdb-only",
            source="test_source",
            step_config=step_config,
            rag_id="rag-docdb-only",
            chunk_count=15,
        )

    # Mock HaikuRAG to return different documents
    mock_doc_common = MagicMock()
    mock_doc_common.id = "rag-common"
    mock_doc_common.uri = "/path/to/common.pdf"
    mock_doc_common.title = "Common Document"
    mock_doc_common.created_at = None
    mock_doc_common.chunk_count = 10
    mock_doc_common.metadata = {}

    mock_doc_lance_only = MagicMock()
    mock_doc_lance_only.id = "rag-lance-only"
    mock_doc_lance_only.uri = "/path/to/lance-only.pdf"
    mock_doc_lance_only.title = "LanceDB Only Document"
    mock_doc_lance_only.created_at = None
    mock_doc_lance_only.chunk_count = 25
    mock_doc_lance_only.metadata = {"extra": "data"}

    mock_client = MagicMock()
    mock_client.list_documents = AsyncMock(return_value=[mock_doc_common, mock_doc_lance_only])

    with (
        patch("soliplex.ingester.lib.operations.get_settings", return_value=mock_settings),
        patch("haiku.rag.client.HaikuRAG") as mock_haiku_rag,
        patch("haiku.rag.config.get_config"),
    ):
        mock_haiku_rag.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_haiku_rag.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await doc_ops.check_rag_db_consistency("discrepancy-db")

    assert "error" not in result
    assert result["documentdb_count"] == 2
    assert result["lancedb_count"] == 2
    assert result["matched"] == 1

    # Check in_documentdb_only
    assert len(result["in_documentdb_only"]) == 1
    assert result["in_documentdb_only"][0]["rag_id"] == "rag-docdb-only"
    assert result["in_documentdb_only"][0]["doc_hash"] == "sha256-docdb-only"

    # Check in_lancedb_only
    assert len(result["in_lancedb_only"]) == 1
    assert result["in_lancedb_only"][0]["rag_id"] == "rag-lance-only"
    assert result["in_lancedb_only"][0]["uri"] == "/path/to/lance-only.pdf"


@pytest.fixture
def temp_lancedb_dir():
    """Create a temporary directory for LanceDB testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def create_embedded_chunk(content: str, order: int, vector_dim: int = 2560):
    """Create a chunk with pre-computed fake embeddings to avoid calling embedding service.

    Note: HaikuRAG requires at least 2560 dimensions for the vector embedding.
    """
    import hashlib

    from haiku.rag.store.models.chunk import Chunk

    # Create a deterministic fake embedding based on content hash
    hash_bytes = hashlib.sha256(content.encode()).digest()
    # Generate fake embedding from hash (normalized to small values)
    # Start with values derived from the hash
    fake_embedding = [(b / 255.0 - 0.5) * 0.1 for b in hash_bytes]
    # Pad to full dimension by repeating the pattern
    while len(fake_embedding) < vector_dim:
        fake_embedding.extend(fake_embedding[: vector_dim - len(fake_embedding)])
    fake_embedding = fake_embedding[:vector_dim]

    return Chunk(
        content=content,
        metadata={},
        order=order,
        embedding=fake_embedding,
    )


@pytest.mark.asyncio
async def test_check_db_with_real_lancedb(db, temp_lancedb_dir):
    """
    Integration test that creates actual LanceDB entries via HaikuRAG directly
    and verifies check_rag_db_consistency works with real data.
    """
    from docling_core.types.doc.document import DoclingDocument
    from haiku.rag.client import HaikuRAG
    from haiku.rag.config import get_config

    db_name = "test-integration-db"

    # Create mock settings pointing to temp directory
    mock_settings = MagicMock()
    mock_settings.lancedb_dir = temp_lancedb_dir

    # Create step_config
    step_config = MagicMock(spec=StepConfig)
    step_config.config_json = {"data_dir": db_name}

    # Create minimal docling document
    docling_doc = DoclingDocument(name="test-doc")

    db_path = Path(temp_lancedb_dir) / db_name
    config = get_config()

    # Create two test documents directly in LanceDB with pre-embedded chunks
    rag_ids = []
    test_doc_hashes = ["sha256-integration-test-0", "sha256-integration-test-1"]

    async with HaikuRAG(db_path=db_path, config=config, create=True) as client:
        for i, doc_hash in enumerate(test_doc_hashes):
            # Create chunks with pre-computed embeddings
            chunks = [create_embedded_chunk(f"Chunk {j} of doc {i}", j) for j in range(3)]

            doc = await client.import_document(
                chunks=chunks,
                title=f"Test Document {i}",
                uri=f"/path/to/test-doc-{i}.pdf",
                metadata={"doc_id": doc_hash, "md5": f"md5-test-{i}", "source": "integration_test"},
                docling_document=docling_doc,
            )
            rag_ids.append(doc.id)

    # Create corresponding DocumentDB records
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        for doc_hash, rag_id in zip(test_doc_hashes, rag_ids, strict=True):
            await rag.create_document_db_record(
                doc_hash=doc_hash,
                source="integration_test",
                step_config=step_config,
                rag_id=rag_id,
                chunk_count=3,
            )

    # Verify LanceDB files were created
    assert db_path.exists(), f"LanceDB path {db_path} should exist"

    # Now test check_rag_db_consistency - should show all matched
    with patch("soliplex.ingester.lib.operations.get_settings", return_value=mock_settings):
        result = await doc_ops.check_rag_db_consistency(db_name, lancedb_dir=temp_lancedb_dir)

    # Verify results
    assert "error" not in result, f"Unexpected error: {result.get('error')}"
    assert result["db_name"] == db_name
    assert result["lancedb_dir"] == temp_lancedb_dir
    assert result["documentdb_count"] == 2
    assert result["lancedb_count"] == 2
    assert result["matched"] == 2
    assert len(result["in_documentdb_only"]) == 0, f"Unexpected in_documentdb_only: {result['in_documentdb_only']}"
    assert len(result["in_lancedb_only"]) == 0, f"Unexpected in_lancedb_only: {result['in_lancedb_only']}"


@pytest.mark.asyncio
async def test_check_db_detects_missing_lancedb_entry(db, temp_lancedb_dir):
    """
    Test that check_db detects when a DocumentDB record exists but LanceDB entry is missing.
    This simulates a case where the LanceDB was corrupted or manually modified.
    """
    from docling_core.types.doc.document import DoclingDocument
    from haiku.rag.client import HaikuRAG
    from haiku.rag.config import get_config

    db_name = "test-missing-lance-db"

    mock_settings = MagicMock()
    mock_settings.lancedb_dir = temp_lancedb_dir

    step_config = MagicMock(spec=StepConfig)
    step_config.config_json = {"data_dir": db_name}

    docling_doc = DoclingDocument(name="test-doc")
    db_path = Path(temp_lancedb_dir) / db_name
    config = get_config()

    # Create one document in LanceDB
    async with HaikuRAG(db_path=db_path, config=config, create=True) as client:
        chunks = [create_embedded_chunk("Test chunk content", 0)]
        doc = await client.import_document(
            chunks=chunks,
            title="Saved Document",
            uri="/path/to/saved.pdf",
            metadata={"doc_id": "sha256-saved-to-lance", "md5": "md5-saved", "source": "test_source"},
            docling_document=docling_doc,
        )
        rag_id1 = doc.id

    # Create DocumentDB records - one for the saved doc, one for a non-existent doc
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        # Create DocumentDB record for saved document
        await rag.create_document_db_record(
            doc_hash="sha256-saved-to-lance",
            source="test_source",
            step_config=step_config,
            rag_id=rag_id1,
            chunk_count=1,
        )

        # Create DocumentDB record for a document that was NEVER saved to LanceDB
        # (simulating a failed save or data inconsistency)
        await rag.create_document_db_record(
            doc_hash="sha256-never-saved",
            source="test_source",
            step_config=step_config,
            rag_id="fake-rag-id-never-existed",
            chunk_count=5,
        )

    # Check consistency - should detect the missing LanceDB entry
    with patch("soliplex.ingester.lib.operations.get_settings", return_value=mock_settings):
        result = await doc_ops.check_rag_db_consistency(db_name, lancedb_dir=temp_lancedb_dir)

    assert "error" not in result
    assert result["documentdb_count"] == 2
    assert result["lancedb_count"] == 1
    assert result["matched"] == 1

    # Should have one document in DocumentDB but not in LanceDB
    assert len(result["in_documentdb_only"]) == 1
    assert result["in_documentdb_only"][0]["rag_id"] == "fake-rag-id-never-existed"
    assert result["in_documentdb_only"][0]["doc_hash"] == "sha256-never-saved"

    # No orphaned LanceDB entries
    assert len(result["in_lancedb_only"]) == 0


@pytest.mark.asyncio
async def test_check_db_detects_orphaned_lancedb_entry(db, temp_lancedb_dir):
    """
    Test that check_db detects when a LanceDB entry exists but DocumentDB record is missing.
    This simulates a case where DocumentDB was cleared but LanceDB wasn't.
    """
    from docling_core.types.doc.document import DoclingDocument
    from haiku.rag.client import HaikuRAG
    from haiku.rag.config import get_config

    db_name = "test-orphaned-lance-db"

    mock_settings = MagicMock()
    mock_settings.lancedb_dir = temp_lancedb_dir

    step_config = MagicMock(spec=StepConfig)
    step_config.config_json = {"data_dir": db_name}

    docling_doc = DoclingDocument(name="test-doc")
    db_path = Path(temp_lancedb_dir) / db_name
    config = get_config()

    # Create two documents in LanceDB
    async with HaikuRAG(db_path=db_path, config=config, create=True) as client:
        # First document - will be tracked
        chunks1 = [create_embedded_chunk("Tracked chunk content", 0)]
        doc1 = await client.import_document(
            chunks=chunks1,
            title="Tracked Document",
            uri="/path/to/tracked.pdf",
            metadata={"doc_id": "sha256-tracked", "md5": "md5-tracked", "source": "test_source"},
            docling_document=docling_doc,
        )
        rag_id1 = doc1.id

        # Second document - will be orphaned (no DocumentDB record)
        chunks2 = [create_embedded_chunk("Orphaned chunk content", 0)]
        doc2 = await client.import_document(
            chunks=chunks2,
            title="Orphaned Document",
            uri="/path/to/orphaned.pdf",
            metadata={"doc_id": "sha256-orphaned", "source": "unknown"},
            docling_document=docling_doc,
        )
        orphan_rag_id = doc2.id

    # Only create DocumentDB record for the first document
    with patch("soliplex.ingester.lib.rag.get_settings", return_value=mock_settings):
        await rag.create_document_db_record(
            doc_hash="sha256-tracked",
            source="test_source",
            step_config=step_config,
            rag_id=rag_id1,
            chunk_count=1,
        )

    # Check consistency - should detect the orphaned LanceDB entry
    with patch("soliplex.ingester.lib.operations.get_settings", return_value=mock_settings):
        result = await doc_ops.check_rag_db_consistency(db_name, lancedb_dir=temp_lancedb_dir)

    assert "error" not in result
    assert result["documentdb_count"] == 1
    assert result["lancedb_count"] == 2
    assert result["matched"] == 1

    # No missing LanceDB entries
    assert len(result["in_documentdb_only"]) == 0

    # Should have one orphaned LanceDB entry
    assert len(result["in_lancedb_only"]) == 1
    assert result["in_lancedb_only"][0]["rag_id"] == orphan_rag_id
    assert result["in_lancedb_only"][0]["uri"] == "/path/to/orphaned.pdf"
