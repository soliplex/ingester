import json
import logging
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

logger = logging.getLogger(__name__)


@pytest.fixture
def test_client():
    """Create a test client with mocked lifespan"""
    with patch("soliplex.ingester.lib.wf.runner.start_worker", new_callable=AsyncMock):
        from soliplex.ingester.server import app

        client = TestClient(app)
        return client


def test_source_status(test_client):
    """Test source_status endpoint"""
    with patch("soliplex.ingester.server.operations.get_doc_status") as mock_status:
        mock_status.return_value = ({"hash1": "status1"}, [])
        response = test_client.post(
            "/api/v1/source-status",
            data={
                "source": "test_source",
                "hashes": json.dumps({"hash1": "v1"}),
            },
        )
        assert response.status_code == 200
        mock_status.assert_called_once()


def test_source_status_invalid_hashes(test_client):
    """Test source_status endpoint with invalid hashes"""
    with patch("soliplex.ingester.server.operations.get_doc_status") as mock_status:
        response = test_client.post(
            "/api/v1/source-status",
            data={"source": "test_source", "hashes": json.dumps("not a dict")},
        )
        assert mock_status
        assert response.status_code == 500


def test_get_batches(test_client):
    """Test get all batches endpoint"""
    with patch("soliplex.ingester.server.routes.batch.operations.list_batches") as mock_list:
        mock_list.return_value = []
        response = test_client.get("/api/v1/batch/")
        assert response.status_code == 200
        mock_list.assert_called_once()


def test_create_batch(test_client):
    """Test create batch endpoint"""
    with patch("soliplex.ingester.server.routes.batch.operations.new_batch") as mock_new:
        mock_new.return_value = 123
        response = test_client.post(
            "/api/v1/batch/",
            data={"source": "test_source", "name": "test_batch"},
        )
        assert response.status_code == 201
        assert response.json() == {"batch_id": 123}
        mock_new.assert_called_once_with("test_source", "test_batch")


def test_start_workflows_success(test_client):
    """Test start workflows endpoint"""
    with patch("soliplex.ingester.server.routes.batch.wf_ops.create_workflow_runs_for_batch") as mock_create:
        mock_run_group = Mock()
        mock_runs = [Mock(), Mock()]
        mock_create.return_value = (mock_run_group, mock_runs)
        response = test_client.post("/api/v1/batch/start-workflows", data={"batch_id": 1})
        assert response.status_code == 201
        assert response.json()["workflows"] == 2


def test_start_workflows_not_found(test_client):
    """Test start workflows endpoint with batch not found"""
    with patch("soliplex.ingester.server.routes.batch.wf_ops.create_workflow_runs_for_batch") as mock_create:
        from soliplex.ingester.lib.wf.operations import NotFoundError

        mock_create.side_effect = NotFoundError("Batch not found")
        response = test_client.post("/api/v1/batch/start-workflows", data={"batch_id": 999})
        assert response.status_code == 404


def test_start_workflows_error(test_client):
    """Test start workflows endpoint with error"""
    with patch("soliplex.ingester.server.routes.batch.wf_ops.create_workflow_runs_for_batch") as mock_create:
        mock_create.side_effect = Exception("Test error")
        response = test_client.post("/api/v1/batch/start-workflows", data={"batch_id": 1})
        assert response.status_code == 500


def test_batch_status_success(test_client):
    """Test batch status endpoint"""
    with patch("soliplex.ingester.server.routes.batch.operations.get_batch") as mock_get_batch:
        with patch("soliplex.ingester.server.routes.batch.operations.get_documents_in_batch") as mock_get_docs:
            with patch("soliplex.ingester.server.routes.batch.wf_ops.get_workflows") as mock_get_wf:
                mock_batch = Mock()
                mock_get_batch.return_value = mock_batch

                mock_doc1 = Mock()
                mock_doc1.rag_id = "rag1"
                mock_doc2 = Mock()
                mock_doc2.rag_id = None
                mock_get_docs.return_value = [mock_doc1, mock_doc2]

                mock_wf = Mock()
                mock_wf.status.value = "completed"
                mock_get_wf.return_value = [mock_wf]

                response = test_client.get("/api/v1/batch/status?batch_id=1")
                assert response.status_code == 200
                data = response.json()
                assert data["document_count"] == 2
                assert data["parsed"] == 1
                assert data["remaining"] == 1


def test_batch_status_not_found(test_client):
    """Test batch status endpoint with batch not found"""
    with patch("soliplex.ingester.server.routes.batch.operations.get_batch") as mock_get_batch:
        mock_get_batch.return_value = None
        response = test_client.get("/api/v1/batch/status?batch_id=999")
        assert response.status_code == 404


def test_get_docs_by_source(test_client):
    """Test get documents by source"""
    with patch("soliplex.ingester.server.routes.document.operations.get_uris_for_source") as mock_get:
        mock_get.return_value = []
        response = test_client.get("/api/v1/document/?source=test_source")
        assert response.status_code == 200
        mock_get.assert_called_once_with("test_source")


def test_get_docs_by_batch(test_client):
    """Test get documents by batch_id"""
    with patch("soliplex.ingester.server.routes.document.operations.get_uris_for_batch") as mock_get:
        mock_get.return_value = []
        response = test_client.get("/api/v1/document/?batch_id=1")
        assert response.status_code == 200
        mock_get.assert_called_once_with(1)


def test_get_docs_no_params(test_client):
    """Test get documents with no parameters"""
    response = test_client.get("/api/v1/document/")
    assert response.status_code == 400


def test_ingest_document_success(test_client):
    """Test ingest document endpoint"""
    with patch("soliplex.ingester.server.routes.document.workflow.initial_load") as mock_load:
        mock_uri = Mock()
        mock_uri.id = 1
        mock_doc = Mock()
        mock_doc.hash = "hash123"
        mock_load.return_value = (mock_uri, mock_doc)

        response = test_client.post(
            "/api/v1/document/ingest-document",
            data={
                "source_uri": "/test.pdf",
                "source": "test",
                "batch_id": 1,
                "doc_meta": '{"key": "value"}',
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["document_hash"] == "hash123"


def test_ingest_document_invalid_json(test_client):
    """Test ingest document with invalid JSON metadata"""
    response = test_client.post(
        "/api/v1/document/ingest-document",
        data={
            "source_uri": "/test.pdf",
            "source": "test",
            "batch_id": 1,
            "doc_meta": "invalid json",
        },
    )
    assert response.status_code == 400


def test_ingest_document_invalid_metadata_type(test_client):
    """Test ingest document with non-dict metadata"""
    response = test_client.post(
        "/api/v1/document/ingest-document",
        data={
            "source_uri": "/test.pdf",
            "source": "test",
            "batch_id": 1,
            "doc_meta": '["not", "a", "dict"]',
        },
    )
    assert response.status_code == 500


def test_ingest_document_key_error(test_client):
    """Test ingest document with KeyError"""
    with patch("soliplex.ingester.server.routes.document.workflow.initial_load") as mock_load:
        mock_load.side_effect = KeyError("test_key")
        response = test_client.post(
            "/api/v1/document/ingest-document",
            data={
                "source_uri": "/test.pdf",
                "source": "test",
                "batch_id": 1,
                "doc_meta": "{}",
            },
        )
        assert response.status_code == 400


def test_ingest_document_exception(test_client):
    """Test ingest document with exception"""
    with patch("soliplex.ingester.server.routes.document.workflow.initial_load") as mock_load:
        mock_load.side_effect = Exception("test error")
        response = test_client.post(
            "/api/v1/document/ingest-document",
            data={
                "source_uri": "/test.pdf",
                "source": "test",
                "batch_id": 1,
                "doc_meta": "{}",
            },
        )
        assert response.status_code == 500


def test_get_run_group_durations(test_client):
    """Test get run group durations endpoint"""
    with patch("soliplex.ingester.server.routes.stats.wf_ops.get_run_group_durations") as mock_get:
        mock_get.return_value = []
        response = test_client.get("/api/v1/stats/durations?run_group_id=1")
        assert response.status_code == 200


def test_get_run_group_durations_error(test_client):
    """Test get run group durations with error"""
    with patch("soliplex.ingester.server.routes.stats.wf_ops.get_run_group_durations") as mock_get:
        mock_get.side_effect = Exception("test error")
        response = test_client.get("/api/v1/stats/durations?run_group_id=1")
        assert response.status_code == 500


def test_get_run_group_step_stats(test_client):
    """Test get run group step stats endpoint"""
    with patch("soliplex.ingester.server.routes.stats.wf_ops.get_step_stats") as mock_get:
        mock_get.return_value = []
        response = test_client.get("/api/v1/stats/step-stats?run_group_id=1")
        assert response.status_code == 200


def test_get_run_group_step_stats_error(test_client):
    """Test get run group step stats with error"""
    with patch("soliplex.ingester.server.routes.stats.wf_ops.get_step_stats") as mock_get:
        mock_get.side_effect = Exception("test error")
        response = test_client.get("/api/v1/stats/step-stats?run_group_id=1")
        assert response.status_code == 500


def test_get_workflows(test_client):
    """Test get workflows endpoint"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_workflows") as mock_get:
        mock_get.return_value = []
        response = test_client.get("/api/v1/workflow/")
        assert response.status_code == 200


def test_get_workflows_for_status(test_client):
    """Test get workflows by status endpoint"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_workflows_for_status") as mock_get:
        mock_get.return_value = []
        response = test_client.get("/api/v1/workflow/by-status?status=completed")
        assert response.status_code == 200


def test_list_workflows_definitions(test_client):
    """Test list workflow definitions endpoint"""
    with patch("soliplex.ingester.server.routes.workflow.wf_registry.load_registry") as mock_load:
        mock_wf1 = Mock()
        mock_wf1.id = "wf1"
        mock_wf1.name = "Workflow 1"
        mock_load.return_value = {"wf1": mock_wf1}
        response = test_client.get("/api/v1/workflow/definitions")
        assert response.status_code == 200
        assert len(response.json()) == 1


def test_get_workflow_def_success(test_client):
    """Test get workflow definition by id"""
    with patch("soliplex.ingester.server.routes.workflow.wf_registry.load_workflow_registry") as mock_load:
        mock_wf = Mock()
        mock_load.return_value = {"wf1": mock_wf}
        response = test_client.get("/api/v1/workflow/definitions/wf1")
        assert response.status_code == 200


def test_get_workflow_def_not_found(test_client):
    """Test get workflow definition not found"""
    with patch("soliplex.ingester.server.routes.workflow.wf_registry.load_workflow_registry") as mock_load:
        mock_load.return_value = {}
        response = test_client.get("/api/v1/workflow/definitions/nonexistent")
        assert response.status_code == 404


def test_list_params(test_client):
    """Test list param sets endpoint"""
    with patch("soliplex.ingester.server.routes.workflow.wf_registry.load_param_registry") as mock_load:
        mock_param = Mock()
        mock_param.id = "p1"
        mock_param.name = "Params 1"
        mock_load.return_value = {"p1": mock_param}
        response = test_client.get("/api/v1/workflow/param-sets")
        assert response.status_code == 200
        assert len(response.json()) == 1


def test_get_param_set_success(test_client):
    """Test get param set by id"""
    with patch("soliplex.ingester.server.routes.workflow.wf_registry.load_param_registry") as mock_load:
        mock_param = Mock()
        mock_load.return_value = {"p1": mock_param}
        response = test_client.get("/api/v1/workflow/param-sets/p1")
        assert response.status_code == 200


def test_get_param_set_not_found(test_client):
    """Test get param set not found"""
    with patch("soliplex.ingester.server.routes.workflow.wf_registry.load_param_registry") as mock_load:
        mock_load.return_value = {}
        response = test_client.get("/api/v1/workflow/param-sets/nonexistent")
        assert response.status_code == 404


def test_get_param_set_by_target(test_client):
    """Test get param set by target"""
    from soliplex.ingester.lib.models import WorkflowStepType

    with patch("soliplex.ingester.server.routes.workflow.wf_registry.load_param_registry") as mock_load:
        mock_param = Mock()
        mock_param.config = {WorkflowStepType.STORE: {"data_dir": "/test/dir"}}
        mock_load.return_value = {"p1": mock_param}
        response = test_client.get("/api/v1/workflow/param_sets/target//test/dir")
        assert response.status_code == 200


def test_get_workflow_status(test_client):
    """Test get workflow status endpoint"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_run_steps") as mock_get:
        mock_get.return_value = []
        response = test_client.get("/api/v1/workflow/steps?status=completed")
        assert response.status_code == 200


def test_get_workflow_status_error(test_client):
    """Test get workflow status with error"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_run_steps") as mock_get:
        mock_get.side_effect = Exception("test error")
        response = test_client.get("/api/v1/workflow/steps?status=completed")
        assert response.status_code == 200
        assert "error" in response.json()


def test_get_workflow_run_groups(test_client):
    """Test get workflow run groups endpoint"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_run_groups_for_batch") as mock_get:
        mock_get.return_value = []
        response = test_client.get("/api/v1/workflow/run-groups")
        assert response.status_code == 200


def test_get_workflow_run_groups_error(test_client):
    """Test get workflow run groups with error"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_run_groups_for_batch") as mock_get:
        mock_get.side_effect = Exception("test error")
        response = test_client.get("/api/v1/workflow/run-groups")
        assert response.status_code == 500


def test_get_workflow_run_group(test_client):
    """Test get workflow run group by id"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_run_group") as mock_get:
        mock_get.return_value = {}
        response = test_client.get("/api/v1/workflow/run_groups/1")
        assert response.status_code == 200


def test_get_workflow_run_group_error(test_client):
    """Test get workflow run group with error"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_run_group") as mock_get:
        mock_get.side_effect = Exception("test error")
        response = test_client.get("/api/v1/workflow/run_groups/1")
        assert response.status_code == 500


def test_get_run_group_stats(test_client):
    """Test get run group stats endpoint"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_run_group_stats") as mock_get:
        mock_get.return_value = {}
        response = test_client.get("/api/v1/workflow/run_groups/1/stats")
        assert response.status_code == 200


def test_get_run_group_stats_error(test_client):
    """Test get run group stats with error"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_run_group_stats") as mock_get:
        mock_get.side_effect = Exception("test error")
        response = test_client.get("/api/v1/workflow/run_groups/1/stats")
        assert response.status_code == 500


def test_get_workflow_runs(test_client):
    """Test get workflow runs endpoint"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_workflow_runs") as mock_get:
        mock_get.return_value = []
        response = test_client.get("/api/v1/workflow/runs?batch_id=1")
        assert response.status_code == 200


def test_get_workflow_runs_error(test_client):
    """Test get workflow runs with error"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_workflow_runs") as mock_get:
        mock_get.side_effect = Exception("test error")
        response = test_client.get("/api/v1/workflow/runs?batch_id=1")
        assert response.status_code == 200
        assert "error" in response.json()


def test_get_workflow_by_id(test_client):
    """Test get workflow by id endpoint"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_workflow_run") as mock_get:
        mock_get.return_value = {}
        response = test_client.get("/api/v1/workflow/runs/1")
        assert response.status_code == 200


def test_get_workflow_by_id_error(test_client):
    """Test get workflow by id with error"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.get_workflow_run") as mock_get:
        mock_get.side_effect = Exception("test error")
        response = test_client.get("/api/v1/workflow/runs/1")
        assert response.status_code == 200
        assert "error" in response.json()


def test_start_workflow(test_client):
    """Test start workflow endpoint"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.create_single_workflow_run") as mock_create:
        mock_create.return_value = {}
        response = test_client.post("/api/v1/workflow/", data={"doc_id": "hash123"})
        assert response.status_code == 201


def test_start_workflow_error(test_client):
    """Test start workflow with error"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.create_single_workflow_run") as mock_create:
        mock_create.side_effect = Exception("test error")
        response = test_client.post("/api/v1/workflow/", data={"doc_id": "hash123"})
        assert response.status_code == 500


def test_retry_workflow(test_client):
    """Test retry workflow endpoint"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.reset_failed_steps") as mock_reset:
        mock_reset.return_value = {}
        response = test_client.post("/api/v1/workflow/retry?run_group_id=1")
        assert response.status_code == 201


def test_retry_workflow_error(test_client):
    """Test retry workflow with error"""
    with patch("soliplex.ingester.server.routes.workflow.wf_ops.reset_failed_steps") as mock_reset:
        mock_reset.side_effect = Exception("test error")
        response = test_client.post("/api/v1/workflow/retry?run_group_id=1")
        assert response.status_code == 500


# validate_storage endpoint tests


def test_validate_storage_success(test_client):
    """Test validate_storage endpoint with all documents present"""
    with patch("soliplex.ingester.server.routes.document.operations.get_batch") as mock_get_batch:
        with patch("soliplex.ingester.server.routes.document.operations.get_documents_in_batch") as mock_get_docs:
            with patch("soliplex.ingester.server.routes.document.get_storage_operator") as mock_get_op:
                mock_get_batch.return_value = Mock()

                mock_doc1 = Mock()
                mock_doc1.hash = "hash1"
                mock_doc2 = Mock()
                mock_doc2.hash = "hash2"
                mock_get_docs.return_value = [mock_doc1, mock_doc2]

                mock_operator = AsyncMock()
                mock_operator.exists.return_value = True
                mock_get_op.return_value = mock_operator

                response = test_client.get("/api/v1/document/validate_storage?batch_id=1")
                assert response.status_code == 200
                data = response.json()
                assert data["batch_id"] == 1
                assert data["total"] == 2
                assert data["valid"] == 2
                assert data["missing"] == 0
                assert data["missing_hashes"] == []


def test_validate_storage_with_missing_documents(test_client):
    """Test validate_storage endpoint with some documents missing from storage"""
    with patch("soliplex.ingester.server.routes.document.operations.get_batch") as mock_get_batch:
        with patch("soliplex.ingester.server.routes.document.operations.get_documents_in_batch") as mock_get_docs:
            with patch("soliplex.ingester.server.routes.document.get_storage_operator") as mock_get_op:
                mock_get_batch.return_value = Mock()

                mock_doc1 = Mock()
                mock_doc1.hash = "hash1"
                mock_doc2 = Mock()
                mock_doc2.hash = "hash2"
                mock_doc3 = Mock()
                mock_doc3.hash = "hash3"
                mock_get_docs.return_value = [mock_doc1, mock_doc2, mock_doc3]

                mock_operator = AsyncMock()
                # hash1 and hash3 exist, hash2 is missing
                mock_operator.exists.side_effect = [True, False, True]
                mock_get_op.return_value = mock_operator

                response = test_client.get("/api/v1/document/validate_storage?batch_id=1")
                assert response.status_code == 200
                data = response.json()
                assert data["batch_id"] == 1
                assert data["total"] == 3
                assert data["valid"] == 2
                assert data["missing"] == 1
                assert data["missing_hashes"] == ["hash2"]


def test_validate_storage_batch_not_found(test_client):
    """Test validate_storage endpoint with non-existent batch"""
    with patch("soliplex.ingester.server.routes.document.operations.get_batch") as mock_get_batch:
        mock_get_batch.return_value = None
        response = test_client.get("/api/v1/document/validate_storage?batch_id=999")
        assert response.status_code == 404
        assert "Batch 999 not found" in response.json()["error"]


def test_validate_storage_empty_batch(test_client):
    """Test validate_storage endpoint with empty batch"""
    with patch("soliplex.ingester.server.routes.document.operations.get_batch") as mock_get_batch:
        with patch("soliplex.ingester.server.routes.document.operations.get_documents_in_batch") as mock_get_docs:
            with patch("soliplex.ingester.server.routes.document.get_storage_operator") as mock_get_op:
                mock_get_batch.return_value = Mock()
                mock_get_docs.return_value = []
                mock_get_op.return_value = AsyncMock()

                response = test_client.get("/api/v1/document/validate_storage?batch_id=1")
                assert response.status_code == 200
                data = response.json()
                assert data["total"] == 0
                assert data["valid"] == 0
                assert data["missing"] == 0


def test_validate_storage_with_cleanup(test_client):
    """Test validate_storage endpoint with clean_up=true"""
    with patch("soliplex.ingester.server.routes.document.operations.get_batch") as mock_get_batch:
        with patch("soliplex.ingester.server.routes.document.operations.get_documents_in_batch") as mock_get_docs:
            with patch("soliplex.ingester.server.routes.document.get_storage_operator") as mock_get_op:
                with patch("soliplex.ingester.server.routes.document.operations.delete_documents_by_hashes") as mock_delete:
                    mock_get_batch.return_value = Mock()

                    mock_doc1 = Mock()
                    mock_doc1.hash = "hash1"
                    mock_doc2 = Mock()
                    mock_doc2.hash = "hash2"
                    mock_get_docs.return_value = [mock_doc1, mock_doc2]

                    mock_operator = AsyncMock()
                    # hash1 exists, hash2 is missing
                    mock_operator.exists.side_effect = [True, False]
                    mock_get_op.return_value = mock_operator

                    mock_delete.return_value = {
                        "deleted_document_uris": 1,
                        "deleted_uri_history": 2,
                        "deleted_documents": 1,
                        "deleted_workflow_runs": 1,
                        "deleted_run_steps": 5,
                        "deleted_lifecycle_history": 3,
                        "total_deleted": 13,
                    }

                    response = test_client.get("/api/v1/document/validate_storage?batch_id=1&clean_up=true")
                    assert response.status_code == 200
                    data = response.json()
                    assert data["missing"] == 1
                    assert data["missing_hashes"] == ["hash2"]
                    assert "cleanup_stats" in data
                    assert data["cleanup_stats"]["deleted_documents"] == 1
                    assert data["cleanup_stats"]["total_deleted"] == 13

                    # Verify delete was called with correct hashes
                    mock_delete.assert_called_once_with(["hash2"])


def test_validate_storage_cleanup_not_called_when_no_missing(test_client):
    """Test validate_storage doesn't call cleanup when no documents are missing"""
    with patch("soliplex.ingester.server.routes.document.operations.get_batch") as mock_get_batch:
        with patch("soliplex.ingester.server.routes.document.operations.get_documents_in_batch") as mock_get_docs:
            with patch("soliplex.ingester.server.routes.document.get_storage_operator") as mock_get_op:
                with patch("soliplex.ingester.server.routes.document.operations.delete_documents_by_hashes") as mock_delete:
                    mock_get_batch.return_value = Mock()

                    mock_doc1 = Mock()
                    mock_doc1.hash = "hash1"
                    mock_get_docs.return_value = [mock_doc1]

                    mock_operator = AsyncMock()
                    mock_operator.exists.return_value = True
                    mock_get_op.return_value = mock_operator

                    response = test_client.get("/api/v1/document/validate_storage?batch_id=1&clean_up=true")
                    assert response.status_code == 200
                    data = response.json()
                    assert data["missing"] == 0
                    assert "cleanup_stats" not in data

                    # Verify delete was NOT called
                    mock_delete.assert_not_called()


def test_validate_storage_cleanup_false_does_not_delete(test_client):
    """Test validate_storage with clean_up=false doesn't delete missing documents"""
    with patch("soliplex.ingester.server.routes.document.operations.get_batch") as mock_get_batch:
        with patch("soliplex.ingester.server.routes.document.operations.get_documents_in_batch") as mock_get_docs:
            with patch("soliplex.ingester.server.routes.document.get_storage_operator") as mock_get_op:
                with patch("soliplex.ingester.server.routes.document.operations.delete_documents_by_hashes") as mock_delete:
                    mock_get_batch.return_value = Mock()

                    mock_doc1 = Mock()
                    mock_doc1.hash = "hash1"
                    mock_get_docs.return_value = [mock_doc1]

                    mock_operator = AsyncMock()
                    mock_operator.exists.return_value = False
                    mock_get_op.return_value = mock_operator

                    response = test_client.get("/api/v1/document/validate_storage?batch_id=1&clean_up=false")
                    assert response.status_code == 200
                    data = response.json()
                    assert data["missing"] == 1
                    assert "cleanup_stats" not in data

                    # Verify delete was NOT called
                    mock_delete.assert_not_called()


def test_validate_storage_error(test_client):
    """Test validate_storage endpoint with error"""
    with patch("soliplex.ingester.server.routes.document.operations.get_batch") as mock_get_batch:
        with patch("soliplex.ingester.server.routes.document.operations.get_documents_in_batch") as mock_get_docs:
            mock_get_batch.return_value = Mock()
            mock_get_docs.side_effect = Exception("Database error")

            response = test_client.get("/api/v1/document/validate_storage?batch_id=1")
            assert response.status_code == 500
            assert "Database error" in response.json()["error"]
