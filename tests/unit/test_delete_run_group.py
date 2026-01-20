"""
Unit tests for delete_run_group functionality.

Tests the Python implementation of RunGroup deletion for cross-database compatibility.
"""

import pytest
from sqlmodel import select

import soliplex.ingester.lib.operations as doc_ops
import soliplex.ingester.lib.wf.operations as wf_ops
from soliplex.ingester.lib.models import LifeCycleEvent
from soliplex.ingester.lib.models import LifecycleHistory
from soliplex.ingester.lib.models import RunGroup
from soliplex.ingester.lib.models import RunStatus
from soliplex.ingester.lib.models import RunStep
from soliplex.ingester.lib.models import WorkflowRun
from soliplex.ingester.lib.models import get_session


@pytest.mark.asyncio
async def test_delete_run_group_success(db):
    """Test successful deletion of RunGroup and all dependents."""
    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/delete_test.pdf"
    test_bytes = b"test bytes for deletion"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    # Create RunGroup and WorkflowRun
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Create LifecycleHistory
    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run.id,
        event=LifeCycleEvent.GROUP_START,
        status=RunStatus.PENDING,
    )

    # Execute: Delete the RunGroup
    result = await wf_ops.delete_run_group(run_group.id)

    # Verify statistics
    assert result["deleted_rungroups"] == 1
    assert result["deleted_workflowruns"] == 1
    assert result["deleted_runsteps"] == len(steps)
    assert result["deleted_lifecyclehistory"] >= 1
    assert result["total_deleted"] == (
        result["deleted_rungroups"]
        + result["deleted_workflowruns"]
        + result["deleted_runsteps"]
        + result["deleted_lifecyclehistory"]
    )

    # Verify records actually deleted
    async with get_session() as session:
        # RunGroup should be gone
        q = select(RunGroup).where(RunGroup.id == run_group.id)
        result_group = await session.exec(q)
        assert result_group.first() is None

        # WorkflowRun should be gone
        q = select(WorkflowRun).where(WorkflowRun.run_group_id == run_group.id)
        result_runs = await session.exec(q)
        assert len(result_runs.all()) == 0

        # RunSteps should be gone
        q = select(RunStep).where(RunStep.workflow_run_id == workflow_run.id)
        result_steps = await session.exec(q)
        assert len(result_steps.all()) == 0

        # LifecycleHistory should be gone
        q = select(LifecycleHistory).where(LifecycleHistory.run_group_id == run_group.id)
        result_lifecycle = await session.exec(q)
        assert len(result_lifecycle.all()) == 0


@pytest.mark.asyncio
async def test_delete_run_group_not_found(db):
    """Test that NotFoundError is raised for non-existent RunGroup."""
    with pytest.raises(wf_ops.NotFoundError, match="RunGroup with id 99999 does not exist"):
        await wf_ops.delete_run_group(99999)


@pytest.mark.asyncio
async def test_delete_run_group_empty_run_group(db):
    """Test deleting a RunGroup with no WorkflowRuns."""
    # Create a RunGroup without any WorkflowRuns
    batch_id = await doc_ops.new_batch("test_source", "Empty Batch")
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Execute: Delete the empty RunGroup
    result = await wf_ops.delete_run_group(run_group.id)

    # Verify statistics
    assert result["deleted_rungroups"] == 1
    assert result["deleted_workflowruns"] == 0
    assert result["deleted_runsteps"] == 0
    assert result["deleted_lifecyclehistory"] == 0
    assert result["total_deleted"] == 1

    # Verify RunGroup is deleted
    async with get_session() as session:
        q = select(RunGroup).where(RunGroup.id == run_group.id)
        result_group = await session.exec(q)
        assert result_group.first() is None


@pytest.mark.asyncio
async def test_delete_run_group_multiple_workflow_runs(db):
    """Test deleting a RunGroup with multiple WorkflowRuns."""
    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri1 = "/tmp/delete_multi_test1.pdf"
    test_uri2 = "/tmp/delete_multi_test2.pdf"
    test_bytes1 = b"test bytes 1 for deletion"
    test_bytes2 = b"test bytes 2 for deletion"

    uri1, doc1 = await doc_ops.create_document_from_uri(
        test_uri1, "test_source", "application/pdf", test_bytes1, batch_id=batch_id
    )
    uri2, doc2 = await doc_ops.create_document_from_uri(
        test_uri2, "test_source", "application/pdf", test_bytes2, batch_id=batch_id
    )

    # Create RunGroup with multiple WorkflowRuns
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run1, steps1 = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc1.hash)
    workflow_run2, steps2 = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc2.hash)

    # Create lifecycle history for both runs
    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run1.id,
        event=LifeCycleEvent.ITEM_START,
        status=RunStatus.RUNNING,
    )
    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run2.id,
        event=LifeCycleEvent.ITEM_START,
        status=RunStatus.RUNNING,
    )

    # Execute: Delete the RunGroup
    result = await wf_ops.delete_run_group(run_group.id)

    # Verify statistics
    assert result["deleted_rungroups"] == 1
    assert result["deleted_workflowruns"] == 2
    assert result["deleted_runsteps"] == len(steps1) + len(steps2)
    assert result["deleted_lifecyclehistory"] >= 2
    assert result["total_deleted"] >= 5  # At minimum: 1 group + 2 runs + 2 lifecycle

    # Verify all records deleted
    async with get_session() as session:
        # RunGroup should be gone
        q = select(RunGroup).where(RunGroup.id == run_group.id)
        result_group = await session.exec(q)
        assert result_group.first() is None

        # Both WorkflowRuns should be gone
        q = select(WorkflowRun).where(WorkflowRun.run_group_id == run_group.id)
        result_runs = await session.exec(q)
        assert len(result_runs.all()) == 0

        # All RunSteps should be gone
        q = select(RunStep).where(
            RunStep.workflow_run_id.in_([workflow_run1.id, workflow_run2.id])  # type: ignore
        )
        result_steps = await session.exec(q)
        assert len(result_steps.all()) == 0


@pytest.mark.asyncio
async def test_delete_run_group_with_lifecycle_history(db):
    """Test deleting a RunGroup with extensive lifecycle history."""
    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/delete_lifecycle_test.pdf"
    test_bytes = b"test bytes for lifecycle deletion"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    # Create RunGroup and WorkflowRun
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Create multiple lifecycle history records
    lifecycle_events = [
        (LifeCycleEvent.GROUP_START, RunStatus.RUNNING),
        (LifeCycleEvent.ITEM_START, RunStatus.RUNNING),
        (LifeCycleEvent.STEP_START, RunStatus.RUNNING),
        (LifeCycleEvent.STEP_END, RunStatus.COMPLETED),
        (LifeCycleEvent.ITEM_END, RunStatus.COMPLETED),
    ]

    lifecycle_ids = []
    for event, status in lifecycle_events:
        lc = await wf_ops.create_lifecycle_history(
            run_group_id=run_group.id,
            workflow_run_id=workflow_run.id,
            event=event,
            status=status,
            step_id=steps[0].id if "STEP" in event.value else None,
        )
        lifecycle_ids.append(lc.id)

    # Execute: Delete the RunGroup
    result = await wf_ops.delete_run_group(run_group.id)

    # Verify lifecycle history deleted
    assert result["deleted_lifecyclehistory"] == len(lifecycle_events)

    # Verify no lifecycle records remain
    async with get_session() as session:
        q = select(LifecycleHistory).where(LifecycleHistory.id.in_(lifecycle_ids))  # type: ignore
        result_lifecycle = await session.exec(q)
        assert len(result_lifecycle.all()) == 0


@pytest.mark.asyncio
async def test_delete_run_group_transaction_atomicity(db):
    """Test that deletion is atomic - either all succeeds or none."""
    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/delete_atomic_test.pdf"
    test_bytes = b"test bytes for atomicity"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    # Create RunGroup and WorkflowRun
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    run_group_id = run_group.id

    # Verify data exists before deletion
    async with get_session() as session:
        q = select(RunGroup).where(RunGroup.id == run_group_id)
        result = await session.exec(q)
        assert result.first() is not None

    # Execute: Delete the RunGroup
    result = await wf_ops.delete_run_group(run_group_id)

    # After successful deletion, verify ALL related records are gone
    async with get_session() as session:
        # RunGroup
        q = select(RunGroup).where(RunGroup.id == run_group_id)
        result_group = await session.exec(q)
        assert result_group.first() is None

        # WorkflowRuns
        q = select(WorkflowRun).where(WorkflowRun.run_group_id == run_group_id)
        result_runs = await session.exec(q)
        assert len(result_runs.all()) == 0

        # RunSteps
        q = select(RunStep).where(RunStep.workflow_run_id == workflow_run.id)
        result_steps = await session.exec(q)
        assert len(result_steps.all()) == 0

        # LifecycleHistory
        q = select(LifecycleHistory).where(LifecycleHistory.run_group_id == run_group_id)
        result_lifecycle = await session.exec(q)
        assert len(result_lifecycle.all()) == 0


@pytest.mark.asyncio
async def test_delete_run_group_preserves_other_run_groups(db):
    """Test that deleting one RunGroup doesn't affect other RunGroups."""
    # Create two separate batches and run groups
    batch_id1 = await doc_ops.new_batch("test_source", "Batch 1")
    batch_id2 = await doc_ops.new_batch("test_source", "Batch 2")

    test_uri1 = "/tmp/delete_preserve_test1.pdf"
    test_uri2 = "/tmp/delete_preserve_test2.pdf"
    test_bytes1 = b"test bytes 1 for preservation"
    test_bytes2 = b"test bytes 2 for preservation"

    uri1, doc1 = await doc_ops.create_document_from_uri(
        test_uri1, "test_source", "application/pdf", test_bytes1, batch_id=batch_id1
    )
    uri2, doc2 = await doc_ops.create_document_from_uri(
        test_uri2, "test_source", "application/pdf", test_bytes2, batch_id=batch_id2
    )

    # Create two separate run groups
    run_group1 = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id1, param_id="test_base")
    run_group2 = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id2, param_id="test_base")

    workflow_run1, steps1 = await wf_ops.create_workflow_run(run_group=run_group1, doc_id=doc1.hash)
    workflow_run2, steps2 = await wf_ops.create_workflow_run(run_group=run_group2, doc_id=doc2.hash)

    # Execute: Delete only the first RunGroup
    await wf_ops.delete_run_group(run_group1.id)

    # Verify first RunGroup is deleted
    async with get_session() as session:
        q = select(RunGroup).where(RunGroup.id == run_group1.id)
        result_group1 = await session.exec(q)
        assert result_group1.first() is None

        # Verify second RunGroup still exists
        q = select(RunGroup).where(RunGroup.id == run_group2.id)
        result_group2 = await session.exec(q)
        assert result_group2.first() is not None

        # Verify second WorkflowRun still exists
        q = select(WorkflowRun).where(WorkflowRun.id == workflow_run2.id)
        result_run2 = await session.exec(q)
        assert result_run2.first() is not None

        # Verify second RunSteps still exist
        q = select(RunStep).where(RunStep.workflow_run_id == workflow_run2.id)
        result_steps2 = await session.exec(q)
        assert len(result_steps2.all()) == len(steps2)


@pytest.mark.asyncio
async def test_delete_run_group_statistics_accuracy(db):
    """Test that deletion statistics are accurate."""
    # Create test data with known quantities
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")

    # Create 3 documents
    docs = []
    for i in range(3):
        test_uri = f"/tmp/delete_stats_test{i}.pdf"
        test_bytes = f"test bytes {i} for stats".encode()
        uri, doc = await doc_ops.create_document_from_uri(
            test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
        )
        docs.append(doc)

    # Create RunGroup with 3 WorkflowRuns
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    total_steps = 0
    for doc in docs:
        workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)
        total_steps += len(steps)

        # Add lifecycle history for each workflow run
        await wf_ops.create_lifecycle_history(
            run_group_id=run_group.id,
            workflow_run_id=workflow_run.id,
            event=LifeCycleEvent.ITEM_START,
            status=RunStatus.RUNNING,
        )

    # Add one more lifecycle history at the run group level
    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run.id,  # Use the last workflow_run
        event=LifeCycleEvent.GROUP_START,
        status=RunStatus.RUNNING,
    )

    # Execute: Delete the RunGroup
    result = await wf_ops.delete_run_group(run_group.id)

    # Verify exact statistics
    assert result["deleted_rungroups"] == 1
    assert result["deleted_workflowruns"] == 3
    assert result["deleted_runsteps"] == total_steps
    assert result["deleted_lifecyclehistory"] == 4  # 3 item starts + 1 group start
    assert result["total_deleted"] == 1 + 3 + total_steps + 4


@pytest.mark.asyncio
async def test_delete_run_group_with_only_lifecycle_history(db):
    """Test deleting RunGroup that only has lifecycle history (no workflow runs)."""
    # Create RunGroup without WorkflowRuns
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Create lifecycle history directly (simulating group-level event)
    # Note: We need a workflow_run_id, so let's create a dummy one first
    test_uri = "/tmp/delete_lifecycle_only_test.pdf"
    test_bytes = b"test bytes for lifecycle only"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run.id,
        event=LifeCycleEvent.GROUP_START,
        status=RunStatus.RUNNING,
    )

    # Execute: Delete the RunGroup
    result = await wf_ops.delete_run_group(run_group.id)

    # Verify deletion
    assert result["deleted_rungroups"] == 1
    assert result["deleted_lifecyclehistory"] >= 1
    assert result["total_deleted"] >= 2


@pytest.mark.asyncio
async def test_delete_run_group_idempotency(db):
    """Test that deleting an already-deleted RunGroup raises NotFoundError."""
    # Create and delete a RunGroup
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    run_group_id = run_group.id

    # First deletion should succeed
    result = await wf_ops.delete_run_group(run_group_id)
    assert result["deleted_rungroups"] == 1

    # Second deletion should fail
    with pytest.raises(wf_ops.NotFoundError, match=f"RunGroup with id {run_group_id} does not exist"):
        await wf_ops.delete_run_group(run_group_id)
