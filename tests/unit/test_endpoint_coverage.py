"""
Unit tests for newly added and fixed endpoints.

Tests cover:
- GET /api/v1/stats/durations (bug fix verification)
- POST /api/v1/document/cleanup-orphans (new endpoint)
- GET /api/v1/batch/{batch_id}/steps (new endpoint)
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from soliplex.ingester.lib import operations as doc_ops
from soliplex.ingester.lib.wf import operations as wf_ops
from soliplex.ingester.server import app


@pytest.fixture
def client():
    """Create test client with auth disabled."""
    with patch("soliplex.ingester.lib.auth.get_settings") as mock_settings:
        settings = mock_settings.return_value
        settings.auth_enabled = False
        yield TestClient(app)


class TestGetRunGroupDurationsBugFix:
    """Tests for GET /api/v1/stats/durations bug fix."""

    def test_passes_correct_parameter(self, client):
        """Test that endpoint passes run_group_id instead of status."""
        with patch("soliplex.ingester.server.routes.stats.wf_ops.get_run_group_durations") as mock_func:
            mock_func.return_value = [{"doc_id": "test", "duration": 100}]

            response = client.get("/api/v1/stats/durations?run_group_id=123")

            # Verify function was called with correct parameter
            mock_func.assert_called_once_with(123)
            assert response.status_code == 200

    def test_error_handling(self, client):
        """Test error handling when function raises exception."""
        with patch("soliplex.ingester.server.routes.stats.wf_ops.get_run_group_durations") as mock_func:
            mock_func.side_effect = RuntimeError("PostgreSQL required")

            response = client.get("/api/v1/stats/durations?run_group_id=123")

            assert response.status_code == 500
            assert "error" in response.json()


class TestCleanupOrphansEndpoint:
    """Tests for POST /api/v1/document/cleanup-orphans."""

    def test_cleanup_orphans_success(self, client):
        """Test successful cleanup of orphaned documents."""
        with patch("soliplex.ingester.server.routes.document.operations.delete_orphaned_documents") as mock_func:
            mock_func.return_value = {"deleted_documents": 5, "deleted_history": 3}

            response = client.post("/api/v1/document/cleanup-orphans")

            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "Orphaned documents cleaned up"
            assert data["statistics"]["deleted_documents"] == 5
            assert data["statistics"]["deleted_history"] == 3
            mock_func.assert_called_once()

    def test_cleanup_orphans_no_orphans(self, client):
        """Test cleanup when no orphans exist."""
        with patch("soliplex.ingester.server.routes.document.operations.delete_orphaned_documents") as mock_func:
            mock_func.return_value = {"deleted_documents": 0, "deleted_history": 0}

            response = client.post("/api/v1/document/cleanup-orphans")

            assert response.status_code == 200
            data = response.json()
            assert data["statistics"]["deleted_documents"] == 0
            assert data["statistics"]["deleted_history"] == 0

    def test_cleanup_orphans_error(self, client):
        """Test error handling during cleanup."""
        with patch("soliplex.ingester.server.routes.document.operations.delete_orphaned_documents") as mock_func:
            mock_func.side_effect = Exception("Database error")

            response = client.post("/api/v1/document/cleanup-orphans")

            assert response.status_code == 500
            assert "error" in response.json()


class TestGetBatchStepsEndpoint:
    """Tests for GET /api/v1/batch/{batch_id}/steps."""

    def test_get_batch_steps_success(self, client):
        """Test retrieving steps for a batch."""
        from soliplex.ingester.lib.models import RunStatus
        from soliplex.ingester.lib.models import RunStep
        from soliplex.ingester.lib.models import WorkflowStepType

        # Mock step data
        mock_step = RunStep(
            id=1,
            workflow_run_id=1,
            step_config_id=1,
            step_type=WorkflowStepType.PARSE,
            workflow_step_number=1,
            workflow_step_name="parse",
            status=RunStatus.COMPLETED,
            priority=0,
            retry=0,
            retries=3,
        )

        with patch("soliplex.ingester.server.routes.batch.wf_ops.get_steps_for_batch") as mock_func:
            mock_func.return_value = [mock_step]

            response = client.get("/api/v1/batch/123/steps")

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["id"] == 1
            assert data[0]["status"] == "COMPLETED"
            mock_func.assert_called_once_with(123)

    def test_get_batch_steps_empty(self, client):
        """Test retrieving steps when batch has no workflows."""
        with patch("soliplex.ingester.server.routes.batch.wf_ops.get_steps_for_batch") as mock_func:
            mock_func.return_value = []

            response = client.get("/api/v1/batch/999/steps")

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 0

    def test_get_batch_steps_error(self, client):
        """Test error handling when getting batch steps."""
        with patch("soliplex.ingester.server.routes.batch.wf_ops.get_steps_for_batch") as mock_func:
            mock_func.side_effect = Exception("Database error")

            response = client.get("/api/v1/batch/123/steps")

            assert response.status_code == 500
            assert "error" in response.json()


class TestEndpointIntegration:
    """Integration tests with database."""

    @pytest.mark.asyncio
    async def test_cleanup_orphans_integration(self, db, client):
        """Test cleanup-orphans endpoint with real database."""
        from soliplex.ingester.lib.models import get_session

        # Create orphaned document
        orphan_hash = "sha256-orphan-endpoint-test"
        async with get_session() as session:
            from soliplex.ingester.lib.models import Document

            orphan_doc = Document(
                hash=orphan_hash,
                mime_type="application/pdf",
                file_size=100,
            )
            session.add(orphan_doc)
            await session.commit()

        # Call endpoint
        response = client.post("/api/v1/document/cleanup-orphans")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["statistics"]["deleted_documents"] == 1
        assert data["statistics"]["deleted_history"] == 0

        # Verify document was deleted
        async with get_session() as session:
            from sqlmodel import select

            from soliplex.ingester.lib.models import Document

            q = select(Document).where(Document.hash == orphan_hash)
            result = await session.exec(q)
            doc = result.first()
            assert doc is None

    @pytest.mark.asyncio
    async def test_get_batch_steps_integration(self, db, client):
        """Test get-batch-steps endpoint with real database."""
        # Create batch and workflow
        batch_id = await doc_ops.new_batch("test_source", "Test Batch")
        uri, doc = await doc_ops.create_document_from_uri(
            "/tmp/test.pdf", "test_source", "application/pdf", b"content", batch_id=batch_id
        )

        run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
        workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

        # Call endpoint
        response = client.get(f"/api/v1/batch/{batch_id}/steps")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        # Verify step data
        step = data[0]
        assert "id" in step
        assert "status" in step
        assert "step_type" in step
