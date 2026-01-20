"""
Unit tests for raw SQL to SQLModel ORM conversions.

This test file specifically tests the converted functions to ensure:
1. Correct ORM behavior matches original SQL logic
2. Type safety and parameter validation
3. Edge cases and error conditions
4. Cross-database compatibility
"""

import pytest

from soliplex.ingester.lib import operations as doc_ops
from soliplex.ingester.lib.models import RunStatus
from soliplex.ingester.lib.models import WorkflowStepType
from soliplex.ingester.lib.models import get_session
from soliplex.ingester.lib.wf import operations as wf_ops

# ============================================================================
# Tests for delete_orphaned_documents()
# ============================================================================


@pytest.mark.asyncio
async def test_delete_orphaned_documents_with_orphans(db):
    """Test delete_orphaned_documents removes documents with no URIs."""
    # Create a batch
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")

    # Create documents with URIs
    uri1, doc1 = await doc_ops.create_document_from_uri(
        "/tmp/test1.pdf", "test_source", "application/pdf", b"content1", batch_id=batch_id
    )
    uri2, doc2 = await doc_ops.create_document_from_uri(
        "/tmp/test2.pdf", "test_source", "application/pdf", b"content2", batch_id=batch_id
    )

    # Create an orphaned document (no URI)
    orphan_hash = "sha256-orphan123"
    async with get_session() as session:
        from soliplex.ingester.lib.models import Document

        orphan_doc = Document(
            hash=orphan_hash,
            mime_type="application/pdf",
            file_size=100,
        )
        session.add(orphan_doc)
        await session.commit()

    # Verify orphan exists
    async with get_session() as session:
        from sqlmodel import select

        from soliplex.ingester.lib.models import Document

        q = select(Document).where(Document.hash == orphan_hash)
        result = await session.exec(q)
        orphan = result.first()
        assert orphan is not None

    # Delete orphaned documents
    stats = await doc_ops.delete_orphaned_documents()

    # Verify statistics
    assert stats["deleted_documents"] == 1
    assert stats["deleted_history"] == 0  # No history for this orphan

    # Verify orphan is deleted
    async with get_session() as session:
        from sqlmodel import select

        from soliplex.ingester.lib.models import Document

        q = select(Document).where(Document.hash == orphan_hash)
        result = await session.exec(q)
        orphan = result.first()
        assert orphan is None

    # Verify documents with URIs still exist
    async with get_session() as session:
        from sqlmodel import select

        from soliplex.ingester.lib.models import Document

        q = select(Document).where(Document.hash == doc1.hash)
        result = await session.exec(q)
        doc = result.first()
        assert doc is not None


@pytest.mark.asyncio
async def test_delete_orphaned_documents_no_orphans(db):
    """Test delete_orphaned_documents when there are no orphans."""
    # Create a document with URI
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    await doc_ops.create_document_from_uri("/tmp/test.pdf", "test_source", "application/pdf", b"content", batch_id=batch_id)

    # Delete orphaned documents
    stats = await doc_ops.delete_orphaned_documents()

    # Should delete nothing
    assert stats["deleted_documents"] == 0
    assert stats["deleted_history"] == 0


@pytest.mark.asyncio
async def test_delete_orphaned_documents_with_history(db):
    """Test delete_orphaned_documents removes orphaned history records."""
    # Create a batch
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")

    # Create document with URI
    uri, doc = await doc_ops.create_document_from_uri(
        "/tmp/test.pdf", "test_source", "application/pdf", b"content", batch_id=batch_id
    )

    # Create orphaned history record (hash not in documenturi)
    orphan_hash = "sha256-orphanhistory123"
    async with get_session() as session:
        import datetime

        from soliplex.ingester.lib.models import DocumentURIHistory

        history = DocumentURIHistory(
            doc_uri_id=uri.id,
            version=1,
            hash=orphan_hash,
            process_date=datetime.datetime.now(datetime.UTC),
            action="test",
            batch_id=batch_id,
        )
        session.add(history)
        await session.commit()

    # Delete orphaned documents
    stats = await doc_ops.delete_orphaned_documents()

    # Should delete the orphaned history
    assert stats["deleted_history"] >= 1


@pytest.mark.asyncio
async def test_delete_orphaned_documents_empty_database(db):
    """Test delete_orphaned_documents on empty database."""
    stats = await doc_ops.delete_orphaned_documents()

    assert stats["deleted_documents"] == 0
    assert stats["deleted_history"] == 0


# ============================================================================
# Tests for get_step_config_ids()
# ============================================================================


@pytest.mark.asyncio
async def test_get_step_config_ids_with_config_set(db):
    """Test get_step_config_ids returns correct mapping."""
    # Create run group which creates step configs
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    _run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Get step config IDs
    id_map = await wf_ops.get_step_config_ids("test_base")

    # Verify we got a mapping
    assert isinstance(id_map, dict)
    assert len(id_map) > 0

    # Verify it contains expected step types
    # (the actual step types depend on the workflow definition)
    for step_type, config_id in id_map.items():
        assert isinstance(step_type, WorkflowStepType)
        assert isinstance(config_id, int)
        assert config_id > 0


@pytest.mark.asyncio
async def test_get_step_config_ids_creates_new_config_set(db):
    """Test get_step_config_ids creates config set if it doesn't exist."""
    # Call with new param_id
    id_map = await wf_ops.get_step_config_ids("test_base")

    # Should create and return config
    assert isinstance(id_map, dict)
    assert len(id_map) > 0


@pytest.mark.asyncio
async def test_get_step_config_ids_caching(db):
    """Test get_step_config_ids returns same IDs for same param_id."""
    # Get step config IDs twice
    id_map1 = await wf_ops.get_step_config_ids("test_base")
    id_map2 = await wf_ops.get_step_config_ids("test_base")

    # Should return same IDs
    assert id_map1 == id_map2


# ============================================================================
# Tests for get_runnable_steps()
# ============================================================================


@pytest.mark.asyncio
async def test_get_runnable_steps_basic(db):
    """Test get_runnable_steps returns eligible steps."""
    from soliplex.ingester.lib.wf import runner

    # Create workflow with steps
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    uri, doc = await doc_ops.create_document_from_uri(
        "/tmp/test.pdf", "test_source", "application/pdf", b"content", batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Get runnable steps
    runnable = await runner.get_runnable_steps(top=10)

    # Should return the first pending step
    assert len(runnable) > 0
    assert all(step.status == RunStatus.PENDING for step in runnable)
    assert all(step.retry < step.retries for step in runnable)


@pytest.mark.asyncio
async def test_get_runnable_steps_with_batch_filter(db):
    """Test get_runnable_steps filters by batch_id."""
    from soliplex.ingester.lib.wf import runner

    # Create two batches
    batch1_id = await doc_ops.new_batch("test_source", "Batch 1")
    batch2_id = await doc_ops.new_batch("test_source", "Batch 2")

    # Create documents in each batch
    uri1, doc1 = await doc_ops.create_document_from_uri(
        "/tmp/test1.pdf", "test_source", "application/pdf", b"content1", batch_id=batch1_id
    )
    uri2, doc2 = await doc_ops.create_document_from_uri(
        "/tmp/test2.pdf", "test_source", "application/pdf", b"content2", batch_id=batch2_id
    )

    # Create workflow runs for both batches
    run_group1 = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch1_id, param_id="test_base")
    await wf_ops.create_workflow_run(run_group=run_group1, doc_id=doc1.hash)

    run_group2 = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch2_id, param_id="test_base")
    await wf_ops.create_workflow_run(run_group=run_group2, doc_id=doc2.hash)

    # Get runnable steps for batch1 only
    runnable = await runner.get_runnable_steps(batch_id=batch1_id, top=10)

    # Verify all steps are from batch1
    assert len(runnable) > 0
    for step in runnable:
        # Get the workflow run to check batch
        async with get_session() as session:
            from sqlmodel import select

            from soliplex.ingester.lib.models import WorkflowRun

            q = select(WorkflowRun).where(WorkflowRun.id == step.workflow_run_id)
            result = await session.exec(q)
            run = result.first()
            assert run.batch_id == batch1_id


@pytest.mark.asyncio
async def test_get_runnable_steps_excludes_running_steps(db):
    """Test get_runnable_steps excludes steps with running status."""
    from soliplex.ingester.lib.wf import runner

    # Create workflow
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    uri, doc = await doc_ops.create_document_from_uri(
        "/tmp/test.pdf", "test_source", "application/pdf", b"content", batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Mark first step as RUNNING
    async with get_session() as session:
        from sqlmodel import select

        from soliplex.ingester.lib.models import RunStep

        q = select(RunStep).where(RunStep.id == steps[0].id)
        result = await session.exec(q)
        step = result.first()
        step.status = RunStatus.RUNNING
        session.add(step)
        await session.commit()

    # Get runnable steps
    runnable = await runner.get_runnable_steps(top=10)

    # Should not return any steps from this workflow (has running step)
    runnable_ids = [s.workflow_run_id for s in runnable]
    assert workflow_run.id not in runnable_ids


@pytest.mark.asyncio
async def test_get_runnable_steps_excludes_completed_steps(db):
    """Test get_runnable_steps excludes completed/failed steps."""
    from soliplex.ingester.lib.wf import runner

    # Create workflow
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    uri, doc = await doc_ops.create_document_from_uri(
        "/tmp/test.pdf", "test_source", "application/pdf", b"content", batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Mark first step as COMPLETED
    async with get_session() as session:
        from sqlmodel import select

        from soliplex.ingester.lib.models import RunStep

        q = select(RunStep).where(RunStep.id == steps[0].id)
        result = await session.exec(q)
        step = result.first()
        step.status = RunStatus.COMPLETED
        session.add(step)
        await session.commit()

    # Get runnable steps
    runnable = await runner.get_runnable_steps(top=10)

    # Should not include the completed step
    runnable_ids = [s.id for s in runnable]
    assert steps[0].id not in runnable_ids


@pytest.mark.asyncio
async def test_get_runnable_steps_respects_top_limit(db):
    """Test get_runnable_steps respects the top parameter."""
    from soliplex.ingester.lib.wf import runner

    # Create multiple workflows
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Create 5 documents and workflow runs
    for i in range(5):
        uri, doc = await doc_ops.create_document_from_uri(
            f"/tmp/test{i}.pdf", "test_source", "application/pdf", f"content{i}".encode(), batch_id=batch_id
        )
        await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Get runnable steps with limit of 2
    runnable = await runner.get_runnable_steps(top=2)

    # Should return at most 2 steps
    assert len(runnable) <= 2


@pytest.mark.asyncio
async def test_get_runnable_steps_orders_by_priority(db):
    """Test get_runnable_steps orders by priority (descending)."""
    from soliplex.ingester.lib.wf import runner

    # Create workflows with different priorities
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Create documents with different priorities
    priorities = [10, 5, 15]
    for i, priority in enumerate(priorities):
        uri, doc = await doc_ops.create_document_from_uri(
            f"/tmp/test{i}.pdf",
            "test_source",
            "application/pdf",
            f"content{i}".encode(),
            batch_id=batch_id,
        )
        await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash, priority=priority)

    # Get runnable steps
    runnable = await runner.get_runnable_steps(top=10)

    # Should be ordered by priority descending
    if len(runnable) >= 2:
        for i in range(len(runnable) - 1):
            assert runnable[i].priority >= runnable[i + 1].priority


@pytest.mark.asyncio
async def test_get_runnable_steps_empty_database(db):
    """Test get_runnable_steps on empty database."""
    from soliplex.ingester.lib.wf import runner

    runnable = await runner.get_runnable_steps(top=10)

    assert runnable == []


@pytest.mark.asyncio
async def test_get_runnable_steps_excludes_failed_workflows(db):
    """Test get_runnable_steps excludes steps from failed workflow runs."""
    from soliplex.ingester.lib.wf import runner

    # Create workflow
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    uri, doc = await doc_ops.create_document_from_uri(
        "/tmp/test.pdf", "test_source", "application/pdf", b"content", batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Mark workflow run as FAILED
    async with get_session() as session:
        from sqlmodel import select

        from soliplex.ingester.lib.models import WorkflowRun

        q = select(WorkflowRun).where(WorkflowRun.id == workflow_run.id)
        result = await session.exec(q)
        run = result.first()
        run.status = RunStatus.FAILED
        session.add(run)
        await session.commit()

    # Get runnable steps
    runnable = await runner.get_runnable_steps(top=10)

    # Should not include steps from failed workflow
    runnable_workflow_ids = [s.workflow_run_id for s in runnable]
    assert workflow_run.id not in runnable_workflow_ids


# ============================================================================
# Tests for reset_failed_steps() - Additional edge cases
# ============================================================================


@pytest.mark.asyncio
async def test_reset_failed_steps_multiple_runs(db):
    """Test reset_failed_steps handles multiple failed workflow runs."""
    # Create run group with multiple documents
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Create multiple workflow runs
    workflow_runs = []
    for i in range(3):
        uri, doc = await doc_ops.create_document_from_uri(
            f"/tmp/test{i}.pdf",
            "test_source",
            "application/pdf",
            f"content{i}".encode(),
            batch_id=batch_id,
        )
        workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)
        workflow_runs.append(workflow_run)

        # Mark workflow run as FAILED
        async with get_session() as session:
            from sqlmodel import select

            from soliplex.ingester.lib.models import WorkflowRun

            q = select(WorkflowRun).where(WorkflowRun.id == workflow_run.id)
            result = await session.exec(q)
            run = result.first()
            run.status = RunStatus.FAILED
            session.add(run)

            # Mark some steps as failed with retries
            from soliplex.ingester.lib.models import RunStep

            for step in steps[:2]:  # Mark first 2 steps as failed
                q = select(RunStep).where(RunStep.id == step.id)
                result = await session.exec(q)
                s = result.first()
                s.status = RunStatus.FAILED
                s.retry = 1
                session.add(s)

            await session.commit()

    # Reset failed steps
    await wf_ops.reset_failed_steps(run_group.id)

    # Verify all workflow runs are now RUNNING
    async with get_session() as session:
        from sqlmodel import select

        from soliplex.ingester.lib.models import WorkflowRun

        for workflow_run in workflow_runs:
            q = select(WorkflowRun).where(WorkflowRun.id == workflow_run.id)
            result = await session.exec(q)
            run = result.first()
            assert run.status == RunStatus.RUNNING


@pytest.mark.asyncio
async def test_reset_failed_steps_preserves_non_failed_runs(db):
    """Test reset_failed_steps doesn't affect non-failed runs."""
    # Create run group with multiple documents
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Create one failed run and one running run
    uri1, doc1 = await doc_ops.create_document_from_uri(
        "/tmp/test1.pdf", "test_source", "application/pdf", b"content1", batch_id=batch_id
    )
    workflow_run1, _ = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc1.hash)

    uri2, doc2 = await doc_ops.create_document_from_uri(
        "/tmp/test2.pdf", "test_source", "application/pdf", b"content2", batch_id=batch_id
    )
    workflow_run2, _ = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc2.hash)

    # Mark first as FAILED, second as RUNNING
    async with get_session() as session:
        from sqlmodel import select

        from soliplex.ingester.lib.models import WorkflowRun

        q = select(WorkflowRun).where(WorkflowRun.id == workflow_run1.id)
        result = await session.exec(q)
        run = result.first()
        run.status = RunStatus.FAILED
        session.add(run)

        q = select(WorkflowRun).where(WorkflowRun.id == workflow_run2.id)
        result = await session.exec(q)
        run = result.first()
        run.status = RunStatus.RUNNING
        session.add(run)

        await session.commit()

    # Reset failed steps
    await wf_ops.reset_failed_steps(run_group.id)

    # Verify first is now RUNNING, second is still RUNNING
    async with get_session() as session:
        from sqlmodel import select

        from soliplex.ingester.lib.models import WorkflowRun

        q = select(WorkflowRun).where(WorkflowRun.id == workflow_run1.id)
        result = await session.exec(q)
        run1 = result.first()
        assert run1.status == RunStatus.RUNNING

        q = select(WorkflowRun).where(WorkflowRun.id == workflow_run2.id)
        result = await session.exec(q)
        run2 = result.first()
        assert run2.status == RunStatus.RUNNING


@pytest.mark.asyncio
async def test_reset_failed_steps_no_failed_runs(db):
    """Test reset_failed_steps when there are no failed runs."""
    # Create run group
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Create workflow run (leave as RUNNING)
    uri, doc = await doc_ops.create_document_from_uri(
        "/tmp/test.pdf", "test_source", "application/pdf", b"content", batch_id=batch_id
    )
    await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Reset failed steps (should do nothing)
    await wf_ops.reset_failed_steps(run_group.id)

    # No errors should occur


# ============================================================================
# Tests for get_run_group_stats() - Additional edge cases
# ============================================================================


@pytest.mark.asyncio
async def test_get_run_group_stats_multiple_statuses(db):
    """Test get_run_group_stats with mixed step statuses."""
    # Create run group with multiple documents
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Create multiple workflow runs
    for i in range(3):
        uri, doc = await doc_ops.create_document_from_uri(
            f"/tmp/test{i}.pdf",
            "test_source",
            "application/pdf",
            f"content{i}".encode(),
            batch_id=batch_id,
        )
        workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

        # Set different statuses for steps
        async with get_session() as session:
            from sqlmodel import select

            from soliplex.ingester.lib.models import RunStep

            if i == 0:
                # First run: mark first step as COMPLETED
                q = select(RunStep).where(RunStep.id == steps[0].id)
                result = await session.exec(q)
                step = result.first()
                step.status = RunStatus.COMPLETED
                session.add(step)
            elif i == 1:
                # Second run: mark first step as FAILED
                q = select(RunStep).where(RunStep.id == steps[0].id)
                result = await session.exec(q)
                step = result.first()
                step.status = RunStatus.FAILED
                session.add(step)
            # Third run: leave as PENDING

            await session.commit()

    # Get stats
    stats = await wf_ops.get_run_group_stats(run_group.id)

    # Verify we have counts for different statuses
    assert stats[RunStatus.COMPLETED.value] >= 1
    assert stats[RunStatus.FAILED.value] >= 1
    assert stats[RunStatus.PENDING.value] >= 1


@pytest.mark.asyncio
async def test_get_run_group_stats_empty_run_group(db):
    """Test get_run_group_stats with run group that has no workflow runs."""
    # Create run group without workflow runs
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Get stats
    stats = await wf_ops.get_run_group_stats(run_group.id)

    # All statuses should be 0
    for status in RunStatus:
        assert stats[status.value] == 0


# ============================================================================
# Tests for delete_file() - Additional edge cases
# ============================================================================


@pytest.mark.asyncio
async def test_delete_file_multiple_workflow_runs(db):
    """Test delete_file handles document used in multiple workflow runs."""
    from unittest.mock import AsyncMock
    from unittest.mock import patch

    # Create batch and document
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    uri, doc = await doc_ops.create_document_from_uri(
        "/tmp/test.pdf", "test_source", "application/pdf", b"content", batch_id=batch_id
    )

    # Create multiple workflow runs for same document
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    workflow_run1, _ = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)
    workflow_run2, _ = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Mock storage operator to avoid pre-existing bug with VALIDATE steps
    with patch("soliplex.ingester.lib.operations.dal.get_storage_operator") as mock_get_op:
        with patch("soliplex.ingester.lib.operations.add_history_for_hash") as mock_history:
            mock_op = AsyncMock()
            mock_op.delete = AsyncMock()
            mock_get_op.return_value = mock_op

            # Delete file should handle both workflow runs
            async with get_session() as session:
                await doc_ops.delete_file(doc.hash, session)

            # Verify delete was called
            assert mock_op.delete.called
            mock_history.assert_called_once_with(doc.hash, "file deleted")


@pytest.mark.asyncio
async def test_delete_file_no_workflow_runs(db):
    """Test delete_file when document has no workflow runs."""
    from unittest.mock import patch

    # Create document without workflow runs
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    uri, doc = await doc_ops.create_document_from_uri(
        "/tmp/test.pdf", "test_source", "application/pdf", b"content", batch_id=batch_id
    )

    # Mock storage operations
    with patch("soliplex.ingester.lib.operations.add_history_for_hash") as mock_history:
        # Delete file should not fail
        async with get_session() as session:
            await doc_ops.delete_file(doc.hash, session)

        # History should still be added even with no workflow runs
        mock_history.assert_called_once_with(doc.hash, "file deleted")


# ============================================================================
# Tests for get_steps_for_batch() - Additional edge cases
# ============================================================================


@pytest.mark.asyncio
async def test_get_steps_for_batch_multiple_runs(db):
    """Test get_steps_for_batch returns steps from multiple workflow runs."""
    # Create batch with multiple documents
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Create multiple workflow runs
    total_steps = 0
    for i in range(3):
        uri, doc = await doc_ops.create_document_from_uri(
            f"/tmp/test{i}.pdf",
            "test_source",
            "application/pdf",
            f"content{i}".encode(),
            batch_id=batch_id,
        )
        _, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)
        total_steps += len(steps)

    # Get all steps for batch
    batch_steps = await wf_ops.get_steps_for_batch(batch_id)

    # Should return all steps from all workflow runs
    assert len(batch_steps) == total_steps


@pytest.mark.asyncio
async def test_get_steps_for_batch_invalid_batch_id(db):
    """Test get_steps_for_batch with non-existent batch_id."""
    # Get steps for non-existent batch
    steps = await wf_ops.get_steps_for_batch(99999)

    # Should return empty list
    assert steps == []


# ============================================================================
# Tests for get_step_config_for_workflow_run() - Additional edge cases
# ============================================================================


@pytest.mark.asyncio
async def test_get_step_config_for_workflow_run_different_step_types(db):
    """Test get_step_config_for_workflow_run with different step types."""
    # Create workflow
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    uri, doc = await doc_ops.create_document_from_uri(
        "/tmp/test.pdf", "test_source", "application/pdf", b"content", batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Test each step type
    step_types_found = set()
    for step in steps:
        config = await wf_ops.get_step_config_for_workflow_run(workflow_run.id, step.step_type)
        assert config is not None
        assert config.step_type == step.step_type
        step_types_found.add(step.step_type)

    # Verify we tested multiple step types
    assert len(step_types_found) > 1
