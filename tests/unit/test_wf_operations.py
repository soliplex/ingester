import datetime
import logging

import pytest

import soliplex.ingester.lib.models as models
import soliplex.ingester.lib.operations as doc_ops
import soliplex.ingester.lib.wf.operations as wf_ops
from soliplex.ingester.lib.models import LifeCycleEvent
from soliplex.ingester.lib.models import RunStatus
from soliplex.ingester.lib.models import WorkflowStepType

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_not_found_error():
    """Test NotFoundError exception"""
    error = wf_ops.NotFoundError("test message")
    assert "test message" in str(error)


@pytest.mark.asyncio
async def test_create_run_group(db):
    """Test create_run_group function"""

    # Create a batch first
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")

    # Create run group
    run_group = await wf_ops.create_run_group(
        workflow_definition_id="batch", batch_id=batch_id, param_id="test_base", name="Test Run Group"
    )

    assert run_group is not None
    assert run_group.id is not None
    assert run_group.workflow_definition_id == "batch"
    assert run_group.batch_id == batch_id
    assert run_group.param_definition_id == "test_base"
    assert run_group.name == "Test Run Group"
    assert run_group.start_date is not None
    assert run_group.created_date is not None


@pytest.mark.asyncio
async def test_create_run_group_with_invalid_batch(db):
    """Test create_run_group with non-existent batch"""

    with pytest.raises(wf_ops.NotFoundError, match="Batch .* not found"):
        await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=99999, param_id="test_base")


@pytest.mark.asyncio
async def test_get_run_group(db):
    """Test get_run_group function"""

    # Create a batch and run group
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    run_group = await wf_ops.create_run_group(
        workflow_definition_id="batch", batch_id=batch_id, param_id="test_base", name="Test Run Group"
    )

    # Get the run group
    retrieved_group = await wf_ops.get_run_group(run_group.id)
    assert retrieved_group is not None
    assert retrieved_group.id == run_group.id
    assert retrieved_group.workflow_definition_id == run_group.workflow_definition_id


@pytest.mark.asyncio
async def test_get_run_group_not_found(db):
    """Test get_run_group with non-existent id"""

    with pytest.raises(wf_ops.NotFoundError, match="run group .* not found"):
        await wf_ops.get_run_group(99999)


@pytest.mark.asyncio
async def test_get_run_groups_for_batch(db):
    """Test get_run_groups_for_batch function"""

    # Create a batch and run groups
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    run_group1 = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    run_group2 = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Get run groups for batch
    groups = await wf_ops.get_run_groups_for_batch(batch_id)
    assert len(groups) >= 2
    group_ids = [g.id for g in groups]
    assert run_group1.id in group_ids
    assert run_group2.id in group_ids


@pytest.mark.asyncio
async def test_get_run_groups_for_batch_no_filter(db):
    """Test get_run_groups_for_batch with no batch_id filter"""

    # Create a batch and run group
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Get all run groups (no filter)
    groups = await wf_ops.get_run_groups_for_batch(None)
    assert len(groups) >= 1


@pytest.mark.asyncio
async def test_create_workflow_run(db):
    """Test create_workflow_run function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/workflow_test.pdf"
    test_bytes = b"test bytes"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    # Create run group
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Create workflow run
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash, priority=5)

    assert workflow_run is not None
    assert workflow_run.id is not None
    assert workflow_run.run_group_id == run_group.id
    assert workflow_run.workflow_definition_id == "batch"
    assert workflow_run.batch_id == batch_id
    assert workflow_run.doc_id == doc.hash
    assert workflow_run.priority == 5
    assert workflow_run.status == RunStatus.PENDING

    # Check steps
    assert steps is not None
    assert len(steps) > 0
    for step in steps:
        assert step.workflow_run_id == workflow_run.id
        assert step.status == RunStatus.PENDING


@pytest.mark.asyncio
async def test_create_single_workflow_run(db):
    """Test create_single_workflow_run function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/single_workflow_test.pdf"
    test_bytes = b"test bytes"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    # Create single workflow run
    workflow_run, steps = await wf_ops.create_single_workflow_run(
        workflow_definition_id="batch", doc_id=doc.hash, priority=3, param_id="test_base"
    )

    assert workflow_run is not None
    assert workflow_run.doc_id == doc.hash
    assert workflow_run.priority == 3
    assert steps is not None
    assert len(steps) > 0


@pytest.mark.asyncio
async def test_create_workflow_runs_for_batch(db):
    """Test create_workflow_runs_for_batch function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri1 = "/tmp/batch_workflow_test1.pdf"
    test_uri2 = "/tmp/batch_workflow_test2.pdf"
    test_bytes1 = b"test bytes 1"  # Different bytes for different hash
    test_bytes2 = b"test bytes 2"  # Different bytes for different hash

    await doc_ops.create_document_from_uri(test_uri1, "test_source", "application/pdf", test_bytes1, batch_id=batch_id)
    await doc_ops.create_document_from_uri(test_uri2, "test_source", "application/pdf", test_bytes2, batch_id=batch_id)

    # Create workflow runs for batch
    run_group, runs = await wf_ops.create_workflow_runs_for_batch(
        batch_id=batch_id, workflow_definition_id="batch", priority=2, param_id="test_base"
    )

    assert run_group is not None
    assert run_group.batch_id == batch_id
    assert runs is not None
    assert len(runs) == 2
    for run in runs:
        assert run.batch_id == batch_id
        assert run.priority == 2


@pytest.mark.asyncio
async def test_get_workflow_run(db):
    """Test get_workflow_run function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/get_workflow_test.pdf"
    test_bytes = b"test bytes"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Get workflow run without steps
    retrieved_run = await wf_ops.get_workflow_run(workflow_run.id, include_steps=False)
    assert retrieved_run is not None
    assert retrieved_run.id == workflow_run.id
    assert retrieved_run.doc_id == doc.hash

    # Get workflow run with steps
    retrieved_run2, retrieved_steps = await wf_ops.get_workflow_run(workflow_run.id, include_steps=True)
    assert retrieved_run2 is not None
    assert retrieved_run2.id == workflow_run.id
    assert retrieved_steps is not None
    assert len(retrieved_steps) > 0


@pytest.mark.asyncio
async def test_get_workflow_run_not_found(db):
    """Test get_workflow_run with non-existent id"""

    with pytest.raises(wf_ops.NotFoundError, match="workflow run .* not found"):
        await wf_ops.get_workflow_run(99999)


@pytest.mark.asyncio
async def test_get_workflows(db):
    """Test get_workflows function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/workflows_test.pdf"
    test_bytes = b"test bytes"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Get workflows for batch
    workflows, total = await wf_ops.get_workflows(batch_id)
    assert len(workflows) >= 1
    assert total >= 1

    # Get all workflows (no filter)
    all_workflows, all_total = await wf_ops.get_workflows(None)
    assert len(all_workflows) >= 1
    assert all_total >= 1


@pytest.mark.asyncio
async def test_get_workflows_with_steps(db):
    """Test get_workflows function with include_steps=True"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/workflows_with_steps_test.pdf"
    test_bytes = b"test bytes"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Get workflows with steps
    workflows_with_steps, total = await wf_ops.get_workflows(batch_id, include_steps=True)
    assert len(workflows_with_steps) >= 1
    assert total >= 1

    # Verify the result is a list of WorkflowRunWithSteps
    from soliplex.ingester.lib.models import WorkflowRun
    from soliplex.ingester.lib.models import WorkflowRunWithSteps

    # Find our specific workflow run in the results
    our_workflow = None
    for wf in workflows_with_steps:
        assert isinstance(wf, WorkflowRunWithSteps)
        if wf.workflow_run.id == workflow_run.id:
            our_workflow = wf
            break

    assert our_workflow is not None, f"Could not find workflow_run.id={workflow_run.id} in results"
    assert our_workflow.steps is not None
    assert len(our_workflow.steps) == len(steps)

    # Verify without include_steps returns plain WorkflowRun
    workflows_without_steps, total_without = await wf_ops.get_workflows(batch_id, include_steps=False)
    assert isinstance(workflows_without_steps[0], WorkflowRun)
    assert total_without >= 1


@pytest.mark.asyncio
async def test_get_workflows_for_status(db):
    """Test get_workflows_for_status function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/status_test.pdf"
    test_bytes = b"test bytes"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Get workflows with PENDING status
    pending_workflows = await wf_ops.get_workflows_for_status(RunStatus.PENDING, batch_id)
    assert len(pending_workflows) >= 1

    # Get workflows with PENDING status (no batch filter)
    all_pending = await wf_ops.get_workflows_for_status(RunStatus.PENDING, None)
    assert len(all_pending) >= 1


@pytest.mark.asyncio
async def test_get_run_step(db):
    """Test get_run_step function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/run_step_test.pdf"
    test_bytes = b"test bytes"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Get the first step
    step = await wf_ops.get_run_step(steps[0].id)
    assert step is not None
    assert step.id == steps[0].id
    assert step.workflow_run_id == workflow_run.id


@pytest.mark.asyncio
async def test_get_run_step_not_found(db):
    """Test get_run_step with non-existent id"""

    with pytest.raises(wf_ops.NotFoundError, match="run step .* not found"):
        await wf_ops.get_run_step(99999)


@pytest.mark.asyncio
async def test_get_run_steps(db):
    """Test get_run_steps function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/run_steps_test.pdf"
    test_bytes = b"test bytes"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Get all PENDING steps
    pending_steps = await wf_ops.get_run_steps(RunStatus.PENDING)
    assert len(pending_steps) >= 1


@pytest.mark.asyncio
async def test_get_steps_for_batch(db):
    """Test get_steps_for_batch function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/steps_for_batch_test.pdf"
    test_bytes = b"test bytes"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Get steps for batch
    batch_steps = await wf_ops.get_steps_for_batch(batch_id)
    assert len(batch_steps) >= len(steps)


@pytest.mark.asyncio
async def test_get_step_config_by_id(db):
    """Test get_step_config_by_id function"""

    # Get step config ids for a param set
    step_config_ids = await wf_ops.get_step_config_ids("test_base")
    assert step_config_ids is not None

    # Get a step config by id
    parse_config_id = step_config_ids[WorkflowStepType.PARSE]
    step_config = await wf_ops.get_step_config_by_id(parse_config_id)

    assert step_config is not None
    assert step_config.id == parse_config_id
    assert step_config.step_type == WorkflowStepType.PARSE


@pytest.mark.asyncio
async def test_get_step_config_by_id_not_found(db):
    """Test get_step_config_by_id with non-existent id"""

    with pytest.raises(wf_ops.NotFoundError, match="step config .* not found"):
        await wf_ops.get_step_config_by_id(99999)


@pytest.mark.asyncio
async def test_get_step_config_for_workflow_run(db):
    """Test get_step_config_for_workflow_run function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/step_config_test.pdf"
    test_bytes = b"test bytes"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Get step config for PARSE step
    parse_config = await wf_ops.get_step_config_for_workflow_run(workflow_run.id, WorkflowStepType.PARSE)
    assert parse_config is not None
    assert parse_config.step_type == WorkflowStepType.PARSE


@pytest.mark.asyncio
async def test_get_step_config_for_workflow_run_not_found(db):
    """Test get_step_config_for_workflow_run with non-existent workflow"""

    with pytest.raises(wf_ops.NotFoundError, match="step config .* not found"):
        await wf_ops.get_step_config_for_workflow_run(99999, WorkflowStepType.PARSE)


@pytest.mark.asyncio
async def test_find_operator_for_workflow_run(db):
    """Test find_operator_for_workflow_run function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/operator_test.pdf"
    test_bytes = b"test bytes"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Find operator for workflow run
    operator = await wf_ops.find_operator_for_workflow_run(
        workflow_run.id, WorkflowStepType.PARSE, models.ArtifactType.PARSED_JSON
    )
    assert operator is not None


@pytest.mark.asyncio
async def test_create_lifecycle_history(db):
    """Test create_lifecycle_history function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/lifecycle_test.pdf"
    test_bytes = b"test bytes"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Create lifecycle history
    history = await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run.id,
        event=LifeCycleEvent.ITEM_START,
        status=RunStatus.RUNNING,
        step_id=steps[0].id,
        handler_name="test_handler",
        status_message="Test message",
        status_meta={"key": "value"},
    )

    assert history is not None
    assert history.run_group_id == run_group.id
    assert history.workflow_run_id == workflow_run.id
    assert history.event == LifeCycleEvent.ITEM_START
    assert history.status == RunStatus.RUNNING
    assert history.step_id == steps[0].id
    assert history.handler_name == "test_handler"
    assert history.status_message == "Test message"


@pytest.mark.asyncio
async def test_update_run_status(db):
    """Test update_run_status function - tests status update logic"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/update_status_test.pdf"
    test_bytes = b"test bytes for update"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Create and update in the same session
    async with models.get_session() as session:
        workflow_run = models.WorkflowRun(
            run_group_id=run_group.id,
            workflow_definition_id="batch",
            batch_id=batch_id,
            doc_id=doc.hash,
            start_date=datetime.datetime.now(),
            priority=0,
            created_date=datetime.datetime.now(),
            run_params={},
        )
        session.add(workflow_run)
        await session.flush()
        await session.refresh(workflow_run)
        workflow_run_id = workflow_run.id

        # Update run status to COMPLETED (last step completed)
        result = await wf_ops.update_run_status(
            workflow_run_id, is_last_step=True, status=RunStatus.COMPLETED, session=session
        )
        assert result == RunStatus.COMPLETED
        await session.commit()


@pytest.mark.asyncio
async def test_update_run_status_failed(db):
    """Test update_run_status with FAILED status"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/update_status_failed_test.pdf"
    test_bytes = b"test bytes for failed"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Create and update in the same session
    async with models.get_session() as session:
        workflow_run = models.WorkflowRun(
            run_group_id=run_group.id,
            workflow_definition_id="batch",
            batch_id=batch_id,
            doc_id=doc.hash,
            start_date=datetime.datetime.now(),
            priority=0,
            created_date=datetime.datetime.now(),
            run_params={},
        )
        session.add(workflow_run)
        await session.flush()
        await session.refresh(workflow_run)
        workflow_run_id = workflow_run.id

        # Update run status to FAILED
        result = await wf_ops.update_run_status(workflow_run_id, is_last_step=False, status=RunStatus.FAILED, session=session)
        assert result == RunStatus.FAILED
        await session.commit()


@pytest.mark.asyncio
async def test_update_run_status_running(db):
    """Test update_run_status with RUNNING status"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/update_status_running_test.pdf"
    test_bytes = b"test bytes for running"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    # Create and update in the same session
    async with models.get_session() as session:
        workflow_run = models.WorkflowRun(
            run_group_id=run_group.id,
            workflow_definition_id="batch",
            batch_id=batch_id,
            doc_id=doc.hash,
            start_date=datetime.datetime.now(),
            priority=0,
            created_date=datetime.datetime.now(),
            run_params={},
        )
        session.add(workflow_run)
        await session.flush()
        await session.refresh(workflow_run)
        workflow_run_id = workflow_run.id

        # Update run status to RUNNING (not last step)
        result = await wf_ops.update_run_status(
            workflow_run_id, is_last_step=False, status=RunStatus.COMPLETED, session=session
        )
        assert result == RunStatus.RUNNING
        await session.commit()


@pytest.mark.asyncio
async def test_get_run_group_stats(db):
    """Test get_run_group_stats function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/stats_test.pdf"
    test_bytes = b"test bytes"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Get stats
    stats = await wf_ops.get_run_group_stats(run_group.id)
    assert stats is not None
    assert isinstance(stats, dict)
    # Should have status counts
    assert "PENDING" in stats


@pytest.mark.asyncio
async def test_update_lifecycle_history(db):
    """Test update_lifecycle_history function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/update_lifecycle_test.pdf"
    test_bytes = b"test bytes for lifecycle"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # First create lifecycle history
    hist = await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run.id,
        event=LifeCycleEvent.ITEM_START,
        status=RunStatus.RUNNING,
        step_id=steps[0].id,
    )

    # Then update it
    await wf_ops.update_lifecycle_history(
        hist_id=hist.id,
        status=RunStatus.COMPLETED,
        status_message="Completed successfully",
        status_meta={"result": "success"},
    )


@pytest.mark.asyncio
async def test_update_lifecycle_history_failed(db):
    """Test update_lifecycle_history with FAILED status"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/update_lifecycle_failed_test.pdf"
    test_bytes = b"test bytes for lifecycle failed"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # First create lifecycle history
    hist = await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run.id,
        event=LifeCycleEvent.ITEM_START,
        status=RunStatus.RUNNING,
        step_id=steps[0].id,
    )

    # Then update it with FAILED status
    await wf_ops.update_lifecycle_history(
        hist_id=hist.id,
        status=RunStatus.FAILED,
        status_message="Failed with error",
    )


@pytest.mark.asyncio
async def test_update_lifecycle_history_running(db):
    """Test update_lifecycle_history with RUNNING status (no end_date set)"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/update_lifecycle_running_test.pdf"
    test_bytes = b"test bytes for lifecycle running"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # First create lifecycle history
    hist = await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run.id,
        event=LifeCycleEvent.STEP_START,
        status=RunStatus.PENDING,
        step_id=steps[0].id,
    )

    # Then update it with RUNNING status (should not set end_date)
    await wf_ops.update_lifecycle_history(
        hist_id=hist.id,
        status=RunStatus.RUNNING,
    )


@pytest.mark.asyncio
async def test_get_workflows_with_pagination(db):
    """Test get_workflows with pagination parameters"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri1 = "/tmp/pagination_test1.pdf"
    test_uri2 = "/tmp/pagination_test2.pdf"
    test_bytes1 = b"test bytes 1 for pagination"
    test_bytes2 = b"test bytes 2 for pagination"

    uri1, doc1 = await doc_ops.create_document_from_uri(
        test_uri1, "test_source", "application/pdf", test_bytes1, batch_id=batch_id
    )
    uri2, doc2 = await doc_ops.create_document_from_uri(
        test_uri2, "test_source", "application/pdf", test_bytes2, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc1.hash)
    await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc2.hash)

    # Get workflows with pagination
    workflows, total = await wf_ops.get_workflows(batch_id, page=1, rows_per_page=1)
    assert len(workflows) == 1
    assert total >= 2


@pytest.mark.asyncio
async def test_get_workflows_for_status_with_pagination(db):
    """Test get_workflows_for_status with pagination parameters"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri1 = "/tmp/status_pagination_test1.pdf"
    test_uri2 = "/tmp/status_pagination_test2.pdf"
    test_bytes1 = b"test bytes 1 for status pagination"
    test_bytes2 = b"test bytes 2 for status pagination"

    uri1, doc1 = await doc_ops.create_document_from_uri(
        test_uri1, "test_source", "application/pdf", test_bytes1, batch_id=batch_id
    )
    uri2, doc2 = await doc_ops.create_document_from_uri(
        test_uri2, "test_source", "application/pdf", test_bytes2, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc1.hash)
    await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc2.hash)

    # Get workflows for status with pagination
    workflows, total = await wf_ops.get_workflows_for_status(RunStatus.PENDING, batch_id, page=1, rows_per_page=1)
    assert len(workflows) == 1
    assert total >= 2


@pytest.mark.asyncio
async def test_get_workflow_runs(db):
    """Test get_workflow_runs function (singular)"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/get_workflow_runs_test.pdf"
    test_bytes = b"test bytes for get_workflow_runs"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Get workflow runs by batch_id
    result = await wf_ops.get_workflow_runs(batch_id)
    assert result is not None
    assert result.batch_id == batch_id


@pytest.mark.asyncio
async def test_get_workflow_runs_not_found(db):
    """Test get_workflow_runs with non-existent batch"""

    with pytest.raises(wf_ops.NotFoundError, match="workflow run .* not found"):
        await wf_ops.get_workflow_runs(99999)


@pytest.mark.asyncio
async def test_get_steps_for_batch_empty(db):
    """Test get_steps_for_batch with no steps"""

    # Create a batch with no workflow runs
    batch_id = await doc_ops.new_batch("test_source", "Empty Batch")

    # Get steps for batch - should return empty list
    steps = await wf_ops.get_steps_for_batch(batch_id)
    assert steps == []


@pytest.mark.asyncio
async def test_get_steps_for_workflow_runs_empty(db):
    """Test get_steps_for_workflow_runs with empty list"""

    # Get steps for empty list
    result = await wf_ops.get_steps_for_workflow_runs([])
    assert result == {}


@pytest.mark.asyncio
async def test_update_run_status_error(db):
    """Test update_run_status with ERROR status"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/update_status_error_test.pdf"
    test_bytes = b"test bytes for error status"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    async with models.get_session() as session:
        workflow_run = models.WorkflowRun(
            run_group_id=run_group.id,
            workflow_definition_id="batch",
            batch_id=batch_id,
            doc_id=doc.hash,
            start_date=datetime.datetime.now(),
            priority=0,
            created_date=datetime.datetime.now(),
            run_params={},
        )
        session.add(workflow_run)
        await session.flush()
        await session.refresh(workflow_run)
        workflow_run_id = workflow_run.id

        # Update run status to ERROR (not last step)
        result = await wf_ops.update_run_status(workflow_run_id, is_last_step=False, status=RunStatus.ERROR, session=session)
        assert result == RunStatus.RUNNING
        await session.commit()


@pytest.mark.asyncio
async def test_update_run_status_pending(db):
    """Test update_run_status with PENDING status (no update)"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/update_status_pending_test.pdf"
    test_bytes = b"test bytes for pending status"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")

    async with models.get_session() as session:
        workflow_run = models.WorkflowRun(
            run_group_id=run_group.id,
            workflow_definition_id="batch",
            batch_id=batch_id,
            doc_id=doc.hash,
            start_date=datetime.datetime.now(),
            priority=0,
            created_date=datetime.datetime.now(),
            run_params={},
        )
        session.add(workflow_run)
        await session.flush()
        await session.refresh(workflow_run)
        workflow_run_id = workflow_run.id

        # Update run status with PENDING (should return status unchanged)
        result = await wf_ops.update_run_status(
            workflow_run_id, is_last_step=False, status=RunStatus.PENDING, session=session
        )
        assert result == RunStatus.PENDING
        await session.commit()


@pytest.mark.asyncio
async def test_get_step_config_ids_with_existing_config(db):
    """Test get_step_config_ids when config already exists (line 124)"""

    # First call creates the config
    id_map1 = await wf_ops.get_step_config_ids("test_base")
    assert id_map1 is not None

    # Second call should hit the existing config branch
    id_map2 = await wf_ops.get_step_config_ids("test_base")
    assert id_map2 is not None

    # IDs should be the same
    for step_type in id_map1:
        assert id_map1[step_type] == id_map2[step_type]


@pytest.mark.asyncio
async def test_get_step_config_ids_existing_step_config(db):
    """Test get_step_config_ids when step config exists but config set is new (line 124)"""
    from unittest.mock import AsyncMock
    from unittest.mock import MagicMock
    from unittest.mock import patch

    # We need to simulate the scenario where:
    # 1. ConfigSet doesn't exist (exist_set is None)
    # 2. But individual StepConfig already exists for some step types

    # Create a mock param set
    mock_param_set = MagicMock()
    mock_param_set.id = "test_new_param"
    mock_param_set.config = {models.WorkflowStepType.INGEST: {"key": "value"}}
    mock_param_set.model_dump_json.return_value = '{"id": "test_new_param", "config": {"ingest": {"key": "value"}}}'

    # Create a mock step config that "already exists"
    mock_existing_step_config = MagicMock()
    mock_existing_step_config.id = 999

    # Track flush calls to simulate database state
    flush_count = [0]

    # Mock session behavior
    mock_session = MagicMock()

    # Setup session.exec to return appropriate results
    async def mock_exec(query):
        result = MagicMock()
        # Check if it's the config set query or step config query
        query_str = str(query)
        if "configset" in query_str.lower() or "config_set" in query_str.lower():
            result.first.return_value = None  # ConfigSet doesn't exist
        elif "stepconfig" in query_str.lower():
            # Return existing step config on first query for each step type
            result.first.return_value = mock_existing_step_config
        else:
            result.first.return_value = None
        return result

    mock_session.exec = mock_exec

    async def mock_flush():
        flush_count[0] += 1

    mock_session.flush = mock_flush
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    async def mock_refresh(obj):
        if hasattr(obj, "id") and obj.id is None:
            obj.id = flush_count[0]

    mock_session.refresh = mock_refresh
    mock_session.no_autoflush = MagicMock()
    mock_session.no_autoflush.__enter__ = MagicMock(return_value=mock_session)
    mock_session.no_autoflush.__exit__ = MagicMock(return_value=None)

    mock_context = MagicMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_session)
    mock_context.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("soliplex.ingester.lib.wf.operations.get_param_set", new=AsyncMock(return_value=mock_param_set)),
        patch("soliplex.ingester.lib.wf.operations.get_session", return_value=mock_context),
    ):
        id_map = await wf_ops.get_step_config_ids("test_new_param")
        # The existing step config id should be used
        assert models.WorkflowStepType.INGEST in id_map
        assert id_map[models.WorkflowStepType.INGEST] == 999


@pytest.mark.asyncio
async def test_create_workflow_run_with_invalid_batch_in_run_group(db):
    """Test create_workflow_run when run_group has invalid batch_id (line 328)"""
    from unittest.mock import AsyncMock
    from unittest.mock import MagicMock
    from unittest.mock import patch

    # Create a mock run_group with an invalid batch_id
    mock_run_group = MagicMock()
    mock_run_group.batch_id = 99999
    mock_run_group.workflow_definition_id = "batch"
    mock_run_group.param_definition_id = "test_base"
    mock_run_group.id = 1

    # Mock get_batch to return None
    with patch.object(wf_ops, "get_batch", new=AsyncMock(return_value=None)):
        with pytest.raises(wf_ops.NotFoundError, match="Batch .* not found"):
            await wf_ops.create_workflow_run(run_group=mock_run_group, doc_id="test_hash")


@pytest.mark.asyncio
async def test_get_run_group_durations(db):
    """Test get_run_group_durations function (mocked due to PostgreSQL-specific SQL)"""
    from unittest.mock import AsyncMock
    from unittest.mock import MagicMock
    from unittest.mock import patch

    # Mock the session and query execution since the SQL uses PostgreSQL-specific syntax
    mock_result = MagicMock()
    mock_result.all.return_value = [("parse", 5, 10.5, 1.2, 5.5, 100, 20, 50.0, 120)]

    mock_session = MagicMock()
    mock_session.exec = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("soliplex.ingester.lib.wf.operations.get_session", return_value=mock_session):
        durations = await wf_ops.get_run_group_durations(1)
        assert isinstance(durations, list)
        assert len(durations) == 1


@pytest.mark.asyncio
async def test_get_step_stats(db):
    """Test get_step_stats function (mocked due to PostgreSQL-specific SQL)"""
    from unittest.mock import AsyncMock
    from unittest.mock import MagicMock
    from unittest.mock import patch

    # Mock the session and query execution since the SQL uses PostgreSQL-specific syntax
    mock_result = MagicMock()
    mock_result.all.return_value = [("Test Batch", "test_base", "parse", "PENDING", 5, 100)]

    mock_session = MagicMock()
    mock_session.exec = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("soliplex.ingester.lib.wf.operations.get_session", return_value=mock_session):
        stats = await wf_ops.get_step_stats(1)
        assert isinstance(stats, list)
        assert len(stats) == 1


@pytest.mark.asyncio
async def test_reset_failed_steps(db):
    """Test reset_failed_steps function"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/reset_failed_test.pdf"
    test_bytes = b"test bytes for reset failed"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Set workflow run and step to FAILED status
    async with models.get_session() as session:
        # Update workflow run status to FAILED
        workflow_run_db = await session.get(models.WorkflowRun, workflow_run.id)
        workflow_run_db.status = RunStatus.FAILED
        session.add(workflow_run_db)

        # Update first step to FAILED
        step_db = await session.get(models.RunStep, steps[0].id)
        step_db.status = RunStatus.FAILED
        session.add(step_db)
        await session.commit()

    # Reset failed steps
    await wf_ops.reset_failed_steps(run_group.id)

    # Verify steps were reset (no exception means success)
    # The function doesn't return anything, so we just verify it doesn't raise


@pytest.mark.asyncio
async def test_get_lifecycle_history_by_workflow_run_id(db):
    """Test retrieving lifecycle history by workflow run ID"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/lifecycle_history_test.pdf"
    test_bytes = b"test bytes for lifecycle history"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Create lifecycle history records
    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run.id,
        event=LifeCycleEvent.ITEM_START,
        status=RunStatus.RUNNING,
    )

    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run.id,
        event=LifeCycleEvent.STEP_START,
        status=RunStatus.RUNNING,
        step_id=steps[0].id,
    )

    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run.id,
        event=LifeCycleEvent.STEP_END,
        status=RunStatus.COMPLETED,
        step_id=steps[0].id,
    )

    # Create a record for different workflow run
    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id, workflow_run_id=999, event=LifeCycleEvent.ITEM_START, status=RunStatus.RUNNING
    )

    # Retrieve history for workflow run
    history = await wf_ops.get_lifecycle_history(workflow_run_id=workflow_run.id)

    # Should return 3 records, sorted by start_date
    assert len(history) == 3
    assert all(h.workflow_run_id == workflow_run.id for h in history)

    # Verify events are in order
    assert history[0].event == LifeCycleEvent.ITEM_START
    assert history[1].event == LifeCycleEvent.STEP_START
    assert history[2].event == LifeCycleEvent.STEP_END


@pytest.mark.asyncio
async def test_get_lifecycle_history_by_run_group_id(db):
    """Test retrieving lifecycle history by run group ID"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri1 = "/tmp/lifecycle_group_test1.pdf"
    test_uri2 = "/tmp/lifecycle_group_test2.pdf"
    test_bytes1 = b"test bytes for lifecycle group 1"
    test_bytes2 = b"test bytes for lifecycle group 2"

    _, doc1 = await doc_ops.create_document_from_uri(
        test_uri1, "test_source", "application/pdf", test_bytes1, batch_id=batch_id
    )
    _, doc2 = await doc_ops.create_document_from_uri(
        test_uri2, "test_source", "application/pdf", test_bytes2, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run1, _ = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc1.hash)
    workflow_run2, _ = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc2.hash)

    # Create lifecycle history for multiple workflow runs in the same group
    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id, workflow_run_id=workflow_run1.id, event=LifeCycleEvent.ITEM_START, status=RunStatus.RUNNING
    )

    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id, workflow_run_id=workflow_run2.id, event=LifeCycleEvent.ITEM_START, status=RunStatus.RUNNING
    )

    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id, workflow_run_id=workflow_run1.id, event=LifeCycleEvent.ITEM_END, status=RunStatus.COMPLETED
    )

    # Retrieve history for run group
    history = await wf_ops.get_lifecycle_history(run_group_id=run_group.id)

    # Should return 3 records for run group
    assert len(history) == 3
    assert all(h.run_group_id == run_group.id for h in history)

    # Verify both workflow runs are represented
    workflow_run_ids = {h.workflow_run_id for h in history}
    assert workflow_run_ids == {workflow_run1.id, workflow_run2.id}


@pytest.mark.asyncio
async def test_get_lifecycle_history_empty_result(db):
    """Test retrieving lifecycle history when no records exist"""

    # Try to get history for non-existent workflow run
    history = await wf_ops.get_lifecycle_history(workflow_run_id=999)

    # Should return empty list
    assert len(history) == 0
    assert history == []


@pytest.mark.asyncio
async def test_get_lifecycle_history_no_parameters(db):
    """Test that get_lifecycle_history raises ValueError when no parameters provided"""

    with pytest.raises(ValueError, match="Must provide either workflow_run_id or run_group_id"):
        await wf_ops.get_lifecycle_history()


@pytest.mark.asyncio
async def test_get_lifecycle_history_with_metadata(db):
    """Test retrieving lifecycle history with status messages and metadata"""

    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/lifecycle_metadata_test.pdf"
    test_bytes = b"test bytes for lifecycle metadata"
    uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, _ = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Create lifecycle history with status info
    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run.id,
        event=LifeCycleEvent.ITEM_START,
        status=RunStatus.RUNNING,
        handler_name="start_handler",
        status_message="Processing started",
        status_meta={"batch_size": "10", "priority": "high"},
    )

    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run.id,
        event=LifeCycleEvent.ITEM_FAILED,
        status=RunStatus.FAILED,
        handler_name="error_handler",
        status_message="Processing failed due to timeout",
        status_meta={"error_code": "TIMEOUT", "retry_count": "3"},
    )

    # Retrieve history
    history = await wf_ops.get_lifecycle_history(workflow_run_id=workflow_run.id)

    assert len(history) == 2

    # Verify first record
    assert history[0].handler_name == "start_handler"
    assert history[0].status_message == "Processing started"
    assert history[0].status_meta["batch_size"] == "10"
    assert history[0].status_meta["priority"] == "high"

    # Verify second record
    assert history[1].event == LifeCycleEvent.ITEM_FAILED
    assert history[1].status == RunStatus.FAILED
    assert history[1].handler_name == "error_handler"
    assert history[1].status_message == "Processing failed due to timeout"
    assert history[1].status_meta["error_code"] == "TIMEOUT"
