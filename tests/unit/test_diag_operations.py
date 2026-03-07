"""
Tests for new data access functions used by the si-diag CLI.

Covers:
- find_document_uris_by_pattern (lib/operations.py)
- get_running_steps_enriched (lib/wf/operations.py)
- get_recent_steps (lib/wf/operations.py)
- get_run_group_details (lib/wf/operations.py) - PostgreSQL guard only
"""

import datetime

import pytest

from soliplex.ingester.lib import models
from soliplex.ingester.lib import operations as doc_ops
from soliplex.ingester.lib.models import Database
from soliplex.ingester.lib.models import RunStatus
from soliplex.ingester.lib.models import WorkflowStepType
from soliplex.ingester.lib.wf import operations as wf_ops
from tests.factories import make_batch
from tests.factories import make_document
from tests.factories import make_document_uri
from tests.factories import make_run_group
from tests.factories import make_run_step
from tests.factories import make_workflow_run

# ── helpers ───────────────────────────────────────────────────────


async def _seed_batch_and_doc(session, uri="/tmp/test.pdf", source="pytest", doc_hash=None):
    """Create a batch, document, and URI in the database."""
    batch = make_batch(source=source)
    session.add(batch)
    await session.flush()
    await session.refresh(batch)

    doc = make_document(hash=doc_hash)
    session.add(doc)
    await session.flush()

    doc_uri = make_document_uri(
        uri=uri,
        source=source,
        doc_hash=doc.hash,
        batch_id=batch.id,
    )
    session.add(doc_uri)
    await session.flush()
    await session.refresh(doc_uri)

    # Expunge so objects are usable after session closes
    session.expunge(batch)
    session.expunge(doc)
    session.expunge(doc_uri)

    return batch, doc, doc_uri


async def _seed_workflow(session, batch, doc, run_group=None):
    """Create a run group, workflow run, step config, and run step."""
    step_config = models.StepConfig(
        step_type=WorkflowStepType.PARSE,
        config_json={"key": "value"},
        created_date=datetime.datetime.now(datetime.UTC),
    )
    session.add(step_config)
    await session.flush()
    await session.refresh(step_config)

    if run_group is None:
        run_group = make_run_group(batch_id=batch.id)
        session.add(run_group)
        await session.flush()
        await session.refresh(run_group)

    wf_run = make_workflow_run(
        doc_id=doc.hash,
        run_group_id=run_group.id,
        batch_id=batch.id,
    )
    session.add(wf_run)
    await session.flush()
    await session.refresh(wf_run)

    run_step = make_run_step(
        workflow_run_id=wf_run.id,
        step_config_id=step_config.id,
        step_type=WorkflowStepType.PARSE,
        status=RunStatus.PENDING,
        workflow_step_number=1,
        workflow_step_name="parse",
    )
    session.add(run_step)
    await session.flush()
    await session.refresh(run_step)

    # Expunge so objects are usable after session closes
    session.expunge(step_config)
    session.expunge(run_group)
    session.expunge(wf_run)
    session.expunge(run_step)

    return run_group, wf_run, step_config, run_step


# ── find_document_uris_by_pattern ─────────────────────────────────


@pytest.mark.asyncio
async def test_find_document_uris_by_pattern_match(db):
    async with Database.session() as session:
        await _seed_batch_and_doc(session, uri="/data/reports/quarterly_2025.pdf")
        await session.commit()

    results = await doc_ops.find_document_uris_by_pattern("quarterly")
    assert len(results) == 1
    assert "quarterly" in results[0].uri


@pytest.mark.asyncio
async def test_find_document_uris_by_pattern_case_insensitive(db):
    async with Database.session() as session:
        await _seed_batch_and_doc(session, uri="/data/Reports/QUARTERLY_2025.pdf")
        await session.commit()

    results = await doc_ops.find_document_uris_by_pattern("quarterly")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_find_document_uris_by_pattern_no_match(db):
    async with Database.session() as session:
        await _seed_batch_and_doc(session, uri="/data/reports/annual.pdf")
        await session.commit()

    results = await doc_ops.find_document_uris_by_pattern("quarterly")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_find_document_uris_by_pattern_multiple_matches(db):
    async with Database.session() as session:
        await _seed_batch_and_doc(
            session,
            uri="/data/reports/quarterly_q1.pdf",
            source="src1",
        )
        await _seed_batch_and_doc(
            session,
            uri="/data/reports/quarterly_q2.pdf",
            source="src2",
        )
        await session.commit()

    results = await doc_ops.find_document_uris_by_pattern("quarterly")
    assert len(results) == 2


# ── get_running_steps_enriched ────────────────────────────────────


@pytest.mark.asyncio
async def test_get_running_steps_enriched_with_running(db):
    async with Database.session() as session:
        batch, doc, doc_uri = await _seed_batch_and_doc(session)
        rg, wf_run, sc, rs = await _seed_workflow(session, batch, doc)
        # Set step to RUNNING with a start_date
        rs.status = RunStatus.RUNNING
        rs.start_date = datetime.datetime.now(datetime.UTC)
        session.add(rs)
        await session.commit()

    rows = await wf_ops.get_running_steps_enriched()
    assert len(rows) == 1
    row = rows[0]
    assert row["workflow_run_id"] == wf_run.id
    assert row["doc_hash"] == doc.hash
    assert row["run_group_id"] == rg.id
    assert row["step_type"] == WorkflowStepType.PARSE
    assert row["elapsed_seconds"] is not None
    assert row["elapsed_seconds"] >= 0


@pytest.mark.asyncio
async def test_get_running_steps_enriched_empty(db):
    rows = await wf_ops.get_running_steps_enriched()
    assert rows == []


@pytest.mark.asyncio
async def test_get_running_steps_enriched_excludes_other_statuses(db):
    async with Database.session() as session:
        batch, doc, doc_uri = await _seed_batch_and_doc(session)
        rg, wf_run, sc, rs = await _seed_workflow(session, batch, doc)
        # Leave step as PENDING (default)
        await session.commit()

    rows = await wf_ops.get_running_steps_enriched()
    assert len(rows) == 0


# ── get_recent_steps ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_recent_steps_within_interval(db):
    async with Database.session() as session:
        batch, doc, doc_uri = await _seed_batch_and_doc(session)
        rg, wf_run, sc, rs = await _seed_workflow(session, batch, doc)
        rs.status = RunStatus.COMPLETED
        rs.status_date = datetime.datetime.now(datetime.UTC)
        rs.start_date = datetime.datetime.now(datetime.UTC)
        session.add(rs)
        await session.commit()

    rows = await wf_ops.get_recent_steps("hour")
    assert len(rows) == 1
    assert rows[0]["workflow_run_id"] == wf_run.id


@pytest.mark.asyncio
async def test_get_recent_steps_outside_interval(db):
    async with Database.session() as session:
        batch, doc, doc_uri = await _seed_batch_and_doc(session)
        rg, wf_run, sc, rs = await _seed_workflow(session, batch, doc)
        rs.status = RunStatus.COMPLETED
        # Set status_date to 2 days ago
        rs.status_date = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=2)
        rs.start_date = rs.status_date
        session.add(rs)
        await session.commit()

    rows = await wf_ops.get_recent_steps("hour")
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_get_recent_steps_with_status_filter(db):
    async with Database.session() as session:
        batch, doc, doc_uri = await _seed_batch_and_doc(session)
        rg, wf_run, sc, rs = await _seed_workflow(session, batch, doc)
        rs.status = RunStatus.COMPLETED
        rs.status_date = datetime.datetime.now(datetime.UTC)
        rs.start_date = datetime.datetime.now(datetime.UTC)
        session.add(rs)
        await session.commit()

    # Should find it with COMPLETED filter
    rows = await wf_ops.get_recent_steps("hour", RunStatus.COMPLETED)
    assert len(rows) == 1

    # Should not find it with RUNNING filter
    rows = await wf_ops.get_recent_steps("hour", RunStatus.RUNNING)
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_get_recent_steps_no_filter(db):
    async with Database.session() as session:
        batch, doc, doc_uri = await _seed_batch_and_doc(session)
        rg, wf_run, sc, rs = await _seed_workflow(session, batch, doc)
        rs.status = RunStatus.RUNNING
        rs.status_date = datetime.datetime.now(datetime.UTC)
        rs.start_date = datetime.datetime.now(datetime.UTC)
        session.add(rs)
        await session.commit()

    rows = await wf_ops.get_recent_steps("day")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_get_recent_steps_invalid_interval(db):
    with pytest.raises(ValueError, match="Invalid interval"):
        await wf_ops.get_recent_steps("century")


# ── get_run_group_details (PostgreSQL guard) ──────────────────────


@pytest.mark.asyncio
async def test_get_run_group_details_non_postgres(db):
    """Verify RuntimeError is raised when using SQLite."""
    with pytest.raises(RuntimeError, match="requires PostgreSQL"):
        await wf_ops.get_run_group_details(1)
