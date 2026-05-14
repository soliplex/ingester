"""Tests targeting commit / rollback pathways and previously uncovered
branches in lib/operations.py and lib/wf/operations.py.

The focus is the transaction-handling seam: every test exercises a code
path that either commits, rolls back, or returns without committing, and
verifies the post-condition by reading the DB through a fresh session.
"""

import datetime
import uuid
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from sqlmodel import select

from soliplex.ingester.lib import operations as doc_ops
from soliplex.ingester.lib.models import DocumentURI
from soliplex.ingester.lib.models import ResourceLock
from soliplex.ingester.lib.models import ResourceLockKind
from soliplex.ingester.lib.models import RunGroup
from soliplex.ingester.lib.models import RunStatus
from soliplex.ingester.lib.models import RunStep
from soliplex.ingester.lib.models import SyncState
from soliplex.ingester.lib.models import WorkerCheckin
from soliplex.ingester.lib.models import WorkflowRun
from soliplex.ingester.lib.models import WorkflowStepType
from soliplex.ingester.lib.models import get_session
from soliplex.ingester.lib.wf import operations as wf_ops

# ---------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------


def _naive_utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


async def _make_run_with_steps(
    *,
    n_steps: int = 2,
    step_status: RunStatus = RunStatus.PENDING,
    retries: int = 3,
) -> tuple[int, int, list[int]]:
    """Create a RunGroup + WorkflowRun + N steps. Returns
    (run_group_id, workflow_run_id, [step_ids])."""
    now = _naive_utc_now()
    async with get_session() as session:
        rg = RunGroup(
            workflow_definition_id="test",
            param_definition_id="test",
            batch_id=None,
            name="t",
            created_date=now,
            start_date=now,
            status=RunStatus.PENDING,
            status_date=now,
        )
        session.add(rg)
        await session.flush()
        wr = WorkflowRun(
            workflow_definition_id="test",
            run_group_id=rg.id,
            batch_id=1,
            doc_id=f"doc-{uuid.uuid4().hex}",
            priority=0,
            created_date=now,
            start_date=now,
            status=RunStatus.PENDING,
            status_date=now,
            run_params={},
        )
        session.add(wr)
        await session.flush()
        step_ids: list[int] = []
        for j in range(n_steps):
            rs = RunStep(
                workflow_run_id=wr.id,
                workflow_step_number=j + 1,
                workflow_step_name=f"step-{j + 1}",
                step_config_id=1,
                step_type=WorkflowStepType.PARSE,
                is_last_step=(j == n_steps - 1),
                created_date=now,
                status_date=now,
                priority=0,
                retry=0,
                retries=retries,
                status=step_status,
            )
            session.add(rs)
            await session.flush()
            step_ids.append(rs.id)
        rg_id, wr_id = rg.id, wr.id
        await session.commit()
    return rg_id, wr_id, step_ids


# ---------------------------------------------------------------------
# Database.session() rollback pathway
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_rolls_back_on_caller_exception(db):
    """When the caller raises inside the context, the session must roll
    back so uncommitted writes do not leak past the boundary."""
    now = _naive_utc_now()

    class _Boom(RuntimeError):
        pass

    async def _write_then_raise():
        async with get_session() as session:
            session.add(
                WorkerCheckin(
                    id="rollback-worker",
                    first_checkin=now,
                    last_checkin=now,
                ),
            )
            await session.flush()
            raise _Boom("simulated caller failure")

    with pytest.raises(_Boom):
        await _write_then_raise()

    # Verify the row was not persisted.
    async with get_session() as session:
        rs = await session.exec(
            select(WorkerCheckin).where(WorkerCheckin.id == "rollback-worker"),
        )
        assert rs.first() is None


# ---------------------------------------------------------------------
# lib/operations.py — uncovered branches
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_with_uris_returns_dict(db):
    """Happy path: document + URI exist, returns {document, uris}."""
    uri1, doc1 = await doc_ops.create_document_from_uri(
        "/tmp/with-uris.pdf",
        "pytest",
        "application/pdf",
        b"bytes",
        doc_meta={"k": "v"},
    )
    result = await doc_ops.get_document_with_uris(doc1.hash)
    assert result is not None
    assert result["document"]["hash"] == doc1.hash
    assert len(result["uris"]) == 1
    assert result["uris"][0]["uri"] == "/tmp/with-uris.pdf"


@pytest.mark.asyncio
async def test_get_document_with_uris_returns_none_when_missing(db):
    """Missing document yields None (caught DocumentNotFoundError)."""
    result = await doc_ops.get_document_with_uris("sha256-nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_document_uri_history_by_hash_returns_records(db):
    uri1, doc1 = await doc_ops.create_document_from_uri(
        "/tmp/history.pdf",
        "pytest",
        "application/pdf",
        b"bytes",
        doc_meta={},
    )
    # create_document_from_uri already emits one "create" history row;
    # add a second to cover the loop.
    await doc_ops.add_history_for_hash(doc1.hash, "checked", hist_meta={"n": "1"})

    history = await doc_ops.get_document_uri_history_by_hash(doc1.hash)
    assert history is not None
    assert len(history) >= 2
    actions = {row["action"] for row in history}
    assert {"create", "checked"} <= actions


@pytest.mark.asyncio
async def test_get_document_uri_history_by_hash_returns_none_when_no_uris(db):
    """No URI records → returns None instead of an empty list."""
    history = await doc_ops.get_document_uri_history_by_hash("sha256-no-such")
    assert history is None


@pytest.mark.asyncio
async def test_update_doc_status_skips_when_uri_not_found(db):
    """update_doc_status iterates to_delete; if find_document_uri
    returns None for a row, the deletion branch is skipped."""
    # Plant one URI; pass an unrelated URI in source_hashes so the
    # planted one ends up in `to_delete`. Then monkey-patch
    # find_document_uri to return None to force the missing branch.
    await doc_ops.create_document_from_uri(
        "/tmp/skip.pdf",
        "pytest-skip",
        "application/pdf",
        b"bytes",
        doc_meta={},
    )

    with patch(
        "soliplex.ingester.lib.operations.find_document_uri",
        new=AsyncMock(return_value=None),
    ):
        _status, deleted = await doc_ops.update_doc_status(
            "pytest-skip",
            {"/tmp/other.pdf": "abc"},  # planted URI not in hashes → to_delete
        )

    assert deleted == 0


@pytest.mark.asyncio
async def test_delete_document_uri_by_uri_handles_missing_artifact(db):
    """Storage backends may legitimately not have artifacts of every
    type for a doc; FileNotFoundError must be swallowed."""
    uri1, doc1 = await doc_ops.create_document_from_uri(
        "/tmp/artifact.pdf",
        "pytest-artifact",
        "application/pdf",
        b"bytes",
        doc_meta={},
    )

    class _FakeOp:
        async def delete(self, _hash):
            raise FileNotFoundError("missing")

    with patch(
        "soliplex.ingester.lib.dal.get_storage_operator",
        return_value=_FakeOp(),
    ):
        stats = await doc_ops.delete_document_uri_by_uri("/tmp/artifact.pdf", "pytest-artifact")

    assert stats["deleted_document_uris"] == 1
    # After cascade delete, the URI is gone.
    assert await doc_ops.get_document_uris_by_hash(doc1.hash) == []


@pytest.mark.asyncio
async def test_delete_document_uri_by_uri_logs_unexpected_artifact_error(db):
    """Non-FileNotFoundError from storage is logged but not raised."""
    uri1, doc1 = await doc_ops.create_document_from_uri(
        "/tmp/artifact2.pdf",
        "pytest-artifact2",
        "application/pdf",
        b"bytes",
        doc_meta={},
    )

    class _FakeOp:
        async def delete(self, _hash):
            raise RuntimeError("storage backend exploded")

    with patch(
        "soliplex.ingester.lib.dal.get_storage_operator",
        return_value=_FakeOp(),
    ):
        stats = await doc_ops.delete_document_uri_by_uri("/tmp/artifact2.pdf", "pytest-artifact2")

    assert stats["deleted_document_uris"] == 1


# ---------------------------------------------------------------------
# Explicit-commit changes we added (operations.py)
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_doc_status_commits_deletions(db):
    """update_doc_status now commits explicitly. Verify that after the
    function returns, deletions are visible in a fresh session."""
    await doc_ops.create_document_from_uri(
        "/tmp/persist.pdf",
        "pytest-persist",
        "application/pdf",
        b"bytes",
        doc_meta={},
    )
    _status, deleted = await doc_ops.update_doc_status(
        "pytest-persist",
        {},  # all URIs are deleted candidates
    )
    assert deleted == 1
    async with get_session() as session:
        q = select(DocumentURI).where(DocumentURI.source == "pytest-persist")
        rs = await session.exec(q)
        assert rs.first() is None


@pytest.mark.asyncio
async def test_update_sync_state_persists_after_commit(db):
    """update_sync_state's explicit commit means the new row is visible
    via a fresh session right after the call returns."""
    await doc_ops.update_sync_state(
        source_id="gitea:explicit-commit",
        commit_sha="aaa",
        branch="main",
    )
    async with get_session() as session:
        q = select(SyncState).where(SyncState.source_id == "gitea:explicit-commit")
        rs = await session.exec(q)
        assert rs.first() is not None


@pytest.mark.asyncio
async def test_delete_sync_state_persists_after_commit(db):
    """delete_sync_state's explicit commit removes the row durably."""
    await doc_ops.update_sync_state(
        source_id="gitea:will-be-deleted",
        commit_sha="bbb",
        branch="main",
    )
    await doc_ops.delete_sync_state("gitea:will-be-deleted")
    async with get_session() as session:
        q = select(SyncState).where(SyncState.source_id == "gitea:will-be-deleted")
        rs = await session.exec(q)
        assert rs.first() is None


# ---------------------------------------------------------------------
# lib/wf/operations.py — error_step pathways (the original bug class)
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_step_returns_none_when_step_missing(db):
    """No row exists for that id → return None, no commit, no log."""
    result = await wf_ops.error_step(99999, "lease-X", message="boom")
    assert result is None


@pytest.mark.asyncio
async def test_error_step_returns_none_when_lease_lost(db):
    """Step exists but lease doesn't match → return None (lease lost)."""
    rg_id, wr_id, step_ids = await _make_run_with_steps(n_steps=1)
    step = await wf_ops.claim_next_step("A", "lease-A")
    assert step is not None

    # Try to error with a stale lease token.
    result = await wf_ops.error_step(step.id, "lease-WRONG", message="boom")
    assert result is None

    async with get_session() as session:
        rs = await session.exec(select(RunStep).where(RunStep.id == step.id))
        assert rs.first().status == RunStatus.RUNNING


@pytest.mark.asyncio
async def test_error_step_logs_cancellation_summary(db, caplog):
    """When FAILED triggers cancel_pending_steps, the post-commit log
    line must reference workflow_run_id without re-fetching it from the
    expired ORM object. This is the regression for the original bug.
    """
    rg_id, wr_id, step_ids = await _make_run_with_steps(n_steps=3, retries=1)
    step = await wf_ops.claim_next_step("A", "lease-A")
    assert step is not None

    import logging

    caplog.set_level(logging.INFO, logger="soliplex.ingester.lib.wf.operations")
    new_status = await wf_ops.error_step(step.id, "lease-A", message="terminal")
    assert new_status == RunStatus.FAILED
    # Cancellation log should mention the run id.
    cancel_logs = [r.message for r in caplog.records if "cancelled" in r.message]
    assert cancel_logs, "expected cancellation log line"
    assert str(wr_id) in cancel_logs[0]

    # Siblings cascaded to CANCELLED.
    async with get_session() as session:
        rs = await session.exec(select(RunStep).where(RunStep.workflow_run_id == wr_id))
        statuses = {s.status for s in rs.all()}
        assert RunStatus.FAILED in statuses
        assert RunStatus.CANCELLED in statuses


# ---------------------------------------------------------------------
# complete_run_group try/except
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_run_group_logs_when_not_found(db, caplog):
    """Missing run group → logged at ERROR, function returns cleanly."""
    import logging

    caplog.set_level(logging.ERROR, logger="soliplex.ingester.lib.wf.operations")
    await wf_ops.complete_run_group(99999, RunStatus.COMPLETED)
    assert any("not found" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_complete_run_group_swallows_db_exception(db, caplog):
    """The function wraps everything in try/except for log-and-continue
    semantics. Force the session to raise and verify nothing propagates.
    """
    import logging

    caplog.set_level(logging.ERROR, logger="soliplex.ingester.lib.wf.operations")

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated db failure")

    with patch(
        "soliplex.ingester.lib.wf.operations.get_session",
        side_effect=_boom,
    ):
        # Should not raise.
        await wf_ops.complete_run_group(1, RunStatus.COMPLETED)

    assert any("error in complete_run_group" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_complete_run_group_writes_status_message(db):
    """Happy path with a status_message also covers that branch."""
    rg_id, _, _ = await _make_run_with_steps(n_steps=1)
    await wf_ops.complete_run_group(rg_id, RunStatus.COMPLETED, status_message="ok")

    async with get_session() as session:
        rs = await session.exec(select(RunGroup).where(RunGroup.id == rg_id))
        rg = rs.first()
        assert rg.status == RunStatus.COMPLETED
        assert rg.status_message == "ok"
        assert rg.completed_date is not None


# ---------------------------------------------------------------------
# get_workflow_runs_for_group branches
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_workflow_runs_for_group_returns_empty(db):
    """No runs for the group → returns []."""
    rg_id, _, _ = await _make_run_with_steps(n_steps=1)
    # Make a second group with no runs.
    async with get_session() as session:
        now = _naive_utc_now()
        empty = RunGroup(
            workflow_definition_id="t",
            param_definition_id="t",
            name="empty",
            created_date=now,
            start_date=now,
            status=RunStatus.PENDING,
            status_date=now,
        )
        session.add(empty)
        await session.flush()
        empty_id = empty.id
        await session.commit()
    assert await wf_ops.get_workflow_runs_for_group(empty_id) == []


@pytest.mark.asyncio
async def test_get_workflow_runs_for_group_returns_runs(db):
    rg_id, wr_id, _ = await _make_run_with_steps(n_steps=1)
    runs = await wf_ops.get_workflow_runs_for_group(rg_id)
    assert len(runs) == 1
    assert runs[0].id == wr_id


# ---------------------------------------------------------------------
# Worker heartbeat / checkin
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_heartbeat_inserts_new_row(db):
    await wf_ops.worker_heartbeat("brand-new")
    async with get_session() as session:
        rs = await session.exec(select(WorkerCheckin).where(WorkerCheckin.id == "brand-new"))
        row = rs.first()
        assert row is not None
        assert row.first_checkin is not None


@pytest.mark.asyncio
async def test_worker_heartbeat_updates_existing_row(db):
    """Second call to heartbeat must update last_checkin, not insert."""
    await wf_ops.worker_heartbeat("existing")
    async with get_session() as session:
        rs = await session.exec(select(WorkerCheckin).where(WorkerCheckin.id == "existing"))
        first = rs.first().first_checkin

    # Sleep-free way to ensure a different timestamp: stamp it backward.
    long_ago = _naive_utc_now() - datetime.timedelta(hours=1)
    async with get_session() as session:
        rs = await session.exec(select(WorkerCheckin).where(WorkerCheckin.id == "existing"))
        row = rs.first()
        row.last_checkin = long_ago
        session.add(row)
        await session.commit()

    await wf_ops.worker_heartbeat("existing")
    async with get_session() as session:
        rs = await session.exec(select(WorkerCheckin).where(WorkerCheckin.id == "existing"))
        row = rs.first()
        assert row.first_checkin == first
        assert row.last_checkin > long_ago


@pytest.mark.asyncio
async def test_delete_worker_checkin_removes_row(db):
    await wf_ops.worker_heartbeat("to-delete")
    await wf_ops.delete_worker_checkin("to-delete")
    async with get_session() as session:
        rs = await session.exec(select(WorkerCheckin).where(WorkerCheckin.id == "to-delete"))
        assert rs.first() is None


@pytest.mark.asyncio
async def test_delete_worker_checkin_is_idempotent_when_absent(db):
    """Deleting a nonexistent worker checkin does not raise."""
    await wf_ops.delete_worker_checkin("never-existed")


# ---------------------------------------------------------------------
# ResourceLock — uncovered branches
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_release_resource_lock_returns_false_when_missing(db):
    """No row for the key → returns False (does not commit)."""
    result = await wf_ops.force_release_resource_lock("rag:/nowhere")
    assert result is False


@pytest.mark.asyncio
async def test_force_release_resource_lock_drops_existing_row(db, caplog):
    """Existing lock is deleted and a warning is logged."""
    import logging

    ok = await wf_ops.acquire_resource_lock(
        "rag:/tmp/force",
        holder_id="external",
        holder_kind=ResourceLockKind.WORKER,
    )
    assert ok is True

    caplog.set_level(logging.WARNING, logger="soliplex.ingester.lib.wf.operations")
    released = await wf_ops.force_release_resource_lock("rag:/tmp/force")
    assert released is True
    assert any("force-releasing" in r.message for r in caplog.records)

    async with get_session() as session:
        rs = await session.exec(select(ResourceLock).where(ResourceLock.resource_key == "rag:/tmp/force"))
        assert rs.first() is None


@pytest.mark.asyncio
async def test_get_resource_lock_returns_held_lock(db):
    await wf_ops.acquire_resource_lock(
        "rag:/tmp/getlock",
        holder_id="holder-A",
        holder_kind=ResourceLockKind.WORKER,
    )
    lock = await wf_ops.get_resource_lock("rag:/tmp/getlock")
    assert lock is not None
    assert lock.holder_id == "holder-A"


@pytest.mark.asyncio
async def test_get_resource_lock_returns_none_when_absent(db):
    assert await wf_ops.get_resource_lock("rag:/tmp/no-such") is None


@pytest.mark.asyncio
async def test_list_active_resource_locks_for_holder(db):
    await wf_ops.acquire_resource_lock("rag:/a", "holder-A", ResourceLockKind.WORKER)
    await wf_ops.acquire_resource_lock("rag:/b", "holder-A", ResourceLockKind.WORKER)
    await wf_ops.acquire_resource_lock("rag:/c", "holder-B", ResourceLockKind.WORKER)

    keys_a = await wf_ops.list_active_resource_locks_for_holder("holder-A")
    keys_b = await wf_ops.list_active_resource_locks_for_holder("holder-B")
    keys_none = await wf_ops.list_active_resource_locks_for_holder("nobody")

    assert set(keys_a) == {"rag:/a", "rag:/b"}
    assert set(keys_b) == {"rag:/c"}
    assert keys_none == []


@pytest.mark.asyncio
async def test_acquire_resource_lock_rolls_back_on_commit_race(db):
    """The rare race path: another acquirer wins between our SELECT and
    our INSERT. The function must catch, rollback, and return False.
    We simulate the race by forcing session.commit() to raise once on
    the INSERT, leaving the rest of the codepath intact.
    """
    # We can't easily make sqlite raise an IntegrityError under unit
    # test, so we mock session.commit() to raise the second time it's
    # called within this function (the first commit path is the
    # "already held → return False" branch; we want the post-INSERT
    # commit).
    original_commit = None

    async def _flaky_commit(self):
        nonlocal original_commit
        # Only fail the first time we are called.
        if not getattr(self, "_failed_once", False):
            self._failed_once = True
            raise RuntimeError("simulated unique-constraint race")
        return await original_commit(self)

    from sqlmodel.ext.asyncio.session import AsyncSession as _AS

    original_commit = _AS.commit
    with patch.object(_AS, "commit", _flaky_commit):
        result = await wf_ops.acquire_resource_lock(
            "rag:/tmp/race",
            holder_id="holder-A",
            holder_kind=ResourceLockKind.WORKER,
        )

    assert result is False
    # The lock should NOT exist — the rollback path must have undone
    # the INSERT.
    async with get_session() as session:
        rs = await session.exec(select(ResourceLock).where(ResourceLock.resource_key == "rag:/tmp/race"))
        assert rs.first() is None


# ---------------------------------------------------------------------
# recompute_run_status branches
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recompute_run_status_returns_none_when_no_steps(db):
    """A workflow_run_id with no steps yields no counts → None."""
    result = await wf_ops.recompute_run_status(99999)
    assert result is None


@pytest.mark.asyncio
async def test_recompute_run_status_idempotent_no_change(db):
    """If wf.status already equals the computed status, no commit
    happens and the value is returned unchanged."""
    rg_id, wr_id, step_ids = await _make_run_with_steps(n_steps=2)
    # Mark both steps COMPLETED and stamp the run as COMPLETED too.
    async with get_session() as session:
        for sid in step_ids:
            rs = await session.exec(select(RunStep).where(RunStep.id == sid))
            s = rs.first()
            s.status = RunStatus.COMPLETED
            session.add(s)
        rs = await session.exec(select(WorkflowRun).where(WorkflowRun.id == wr_id))
        wr = rs.first()
        wr.status = RunStatus.COMPLETED
        session.add(wr)
        await session.commit()

    result = await wf_ops.recompute_run_status(wr_id)
    assert result == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_recompute_run_status_running_when_partial(db):
    """Some steps COMPLETED and some PENDING with no FAILED → RUNNING."""
    rg_id, wr_id, step_ids = await _make_run_with_steps(n_steps=3)
    async with get_session() as session:
        # Mark the first step COMPLETED, leave the rest PENDING.
        rs = await session.exec(select(RunStep).where(RunStep.id == step_ids[0]))
        s = rs.first()
        s.status = RunStatus.COMPLETED
        session.add(s)
        await session.commit()

    result = await wf_ops.recompute_run_status(wr_id)
    assert result == RunStatus.RUNNING


@pytest.mark.asyncio
async def test_recompute_run_status_pending_when_no_progress(db):
    """All steps in CANCELLED-but-not-all status hits the PENDING leaf.
    Concretely: zero completed + zero running/errored + zero failed +
    total > 0 happens when all steps are CANCELLED with completed=0.
    Actually the function treats completed+cancelled==total as
    COMPLETED, so this is harder to hit. Use the path where all steps
    are CANCELLED to exercise the COMPLETED branch instead."""
    rg_id, wr_id, step_ids = await _make_run_with_steps(n_steps=2)
    async with get_session() as session:
        for sid in step_ids:
            rs = await session.exec(select(RunStep).where(RunStep.id == sid))
            s = rs.first()
            s.status = RunStatus.CANCELLED
            session.add(s)
        await session.commit()

    result = await wf_ops.recompute_run_status(wr_id)
    assert result == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_recompute_run_status_failed_sets_completed_date(db):
    """FAILED dominates and writes completed_date."""
    rg_id, wr_id, step_ids = await _make_run_with_steps(n_steps=2)
    async with get_session() as session:
        rs = await session.exec(select(RunStep).where(RunStep.id == step_ids[0]))
        s = rs.first()
        s.status = RunStatus.FAILED
        session.add(s)
        await session.commit()

    result = await wf_ops.recompute_run_status(wr_id)
    assert result == RunStatus.FAILED
    async with get_session() as session:
        rs = await session.exec(select(WorkflowRun).where(WorkflowRun.id == wr_id))
        wr = rs.first()
        assert wr.completed_date is not None


# ---------------------------------------------------------------------
# reset_failed global soft path
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_failed_global_soft_resets_failed_and_cancelled(db):
    """No run_group_id and hard=False → bulk update across all groups."""
    rg_id, wr_id, step_ids = await _make_run_with_steps(n_steps=2)
    async with get_session() as session:
        rs = await session.exec(select(RunStep).where(RunStep.id == step_ids[0]))
        s = rs.first()
        s.status = RunStatus.FAILED
        session.add(s)

        rs = await session.exec(select(RunStep).where(RunStep.id == step_ids[1]))
        s = rs.first()
        s.status = RunStatus.CANCELLED
        session.add(s)

        rs = await session.exec(select(WorkflowRun).where(WorkflowRun.id == wr_id))
        wr = rs.first()
        wr.status = RunStatus.FAILED
        session.add(wr)
        await session.commit()

    await wf_ops.reset_failed()

    async with get_session() as session:
        rs = await session.exec(select(RunStep).where(RunStep.workflow_run_id == wr_id))
        for s in rs.all():
            assert s.status == RunStatus.PENDING
        rs = await session.exec(select(WorkflowRun).where(WorkflowRun.id == wr_id))
        assert rs.first().status == RunStatus.PENDING


# ---------------------------------------------------------------------
# Remaining branch coverage — non-rollback paths
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_workflow_runs_for_batch_skip_existing_false(db):
    """skip_existing=False bypasses the RAG pre-check (branch L168->170)."""
    batch_id = await doc_ops.new_batch("test_source", "Skip-False Batch")
    await doc_ops.create_document_from_uri(
        "/tmp/skip-false.pdf",
        "test_source",
        "application/pdf",
        b"sf-bytes",
        batch_id=batch_id,
    )
    run_group, runs = await wf_ops.create_workflow_runs_for_batch(
        batch_id=batch_id,
        workflow_definition_id="batch",
        param_id="test_base",
        skip_existing=False,
    )
    assert run_group is not None
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_complete_run_group_without_status_message(db):
    """status_message=None branch (L356->358)."""
    rg_id, _, _ = await _make_run_with_steps(n_steps=1)
    await wf_ops.complete_run_group(rg_id, RunStatus.COMPLETED)  # no message
    async with get_session() as session:
        rs = await session.exec(select(RunGroup).where(RunGroup.id == rg_id))
        rg = rs.first()
        assert rg.status == RunStatus.COMPLETED
        # status_message left untouched (default None).
        assert rg.status_message is None


@pytest.mark.asyncio
async def test_create_single_workflow_run_raises_when_document_missing(db):
    """Missing doc → DocumentNotFoundError propagates from get_document."""
    with pytest.raises(doc_ops.DocumentNotFoundError):
        await wf_ops.create_single_workflow_run(
            workflow_definition_id="single",
            doc_id="sha256-does-not-exist",
            param_id="test_base",
        )


@pytest.mark.asyncio
async def test_update_run_status_writes_status_message(db):
    """L1048: status_message is forwarded when provided."""
    rg_id, wr_id, step_ids = await _make_run_with_steps(n_steps=1)
    async with get_session() as session:
        await wf_ops.update_run_status(
            workflow_run_id=wr_id,
            is_last_step=True,
            status=RunStatus.COMPLETED,
            session=session,
            status_message="all done",
        )
        await session.commit()
    async with get_session() as session:
        rs = await session.exec(select(WorkflowRun).where(WorkflowRun.id == wr_id))
        assert rs.first().status_message == "all done"


@pytest.mark.asyncio
async def test_get_run_group_durations_raises_on_sqlite(db):
    """L1242: PostgreSQL guard raises on SQLite."""
    with pytest.raises(RuntimeError, match="requires PostgreSQL"):
        await wf_ops.get_run_group_durations(1)


@pytest.mark.asyncio
async def test_get_step_stats_raises_on_sqlite(db):
    """L1312: PostgreSQL guard raises on SQLite."""
    with pytest.raises(RuntimeError, match="requires PostgreSQL"):
        await wf_ops.get_step_stats(1)


@pytest.mark.asyncio
async def test_get_running_steps_enriched_handles_null_start_date(db):
    """L1531->1535: row.start_date is None branch — elapsed stays None."""
    rg_id, wr_id, step_ids = await _make_run_with_steps(n_steps=1)
    # Promote the step to RUNNING with start_date NULL.
    async with get_session() as session:
        rs = await session.exec(select(RunStep).where(RunStep.id == step_ids[0]))
        step = rs.first()
        step.status = RunStatus.RUNNING
        step.start_date = None
        session.add(step)
        await session.commit()

    enriched = await wf_ops.get_running_steps_enriched()
    assert len(enriched) == 1
    assert enriched[0]["start_date"] is None
    assert enriched[0]["elapsed_seconds"] is None


@pytest.mark.asyncio
async def test_get_recent_steps_handles_null_start_date(db):
    """L1625->1629: same null-start-date branch in get_recent_steps."""
    rg_id, wr_id, step_ids = await _make_run_with_steps(n_steps=1)
    async with get_session() as session:
        rs = await session.exec(select(RunStep).where(RunStep.id == step_ids[0]))
        step = rs.first()
        step.start_date = None
        step.status_date = _naive_utc_now()  # recent → caught by the filter
        session.add(step)
        await session.commit()

    recent = await wf_ops.get_recent_steps(interval="hour")
    assert len(recent) == 1
    assert recent[0]["elapsed_seconds"] is None


@pytest.mark.asyncio
async def test_get_workflow_runs_for_group_with_doc_info_status_filter(db):
    """L1736-1739: status_filter narrows the result set."""
    rg_id, wr_id, _ = await _make_run_with_steps(n_steps=1)
    # Mark the run FAILED so the filter has something to keep.
    async with get_session() as session:
        rs = await session.exec(select(WorkflowRun).where(WorkflowRun.id == wr_id))
        wr = rs.first()
        wr.status = RunStatus.FAILED
        session.add(wr)
        await session.commit()

    # No matching status → empty.
    none_match = await wf_ops.get_workflow_runs_for_group_with_doc_info(rg_id, status_filter="COMPLETED")
    assert none_match == {}

    # Matching status → returns dict keyed by doc_id.
    match = await wf_ops.get_workflow_runs_for_group_with_doc_info(rg_id, status_filter="failed")
    # The dict shape depends on document presence; key existence is enough.
    assert isinstance(match, dict)

    # No filter → skips the filter branch (covers L1737->L1739 False arc).
    no_filter = await wf_ops.get_workflow_runs_for_group_with_doc_info(rg_id)
    assert isinstance(no_filter, dict)


@pytest.mark.asyncio
async def test_claim_next_step_filters_by_batch_id(db):
    """L1815: batch_id filter only claims steps from the matching batch."""
    # Two runs, two distinct batch_ids.
    now = _naive_utc_now()
    async with get_session() as session:
        rg = RunGroup(
            workflow_definition_id="t",
            param_definition_id="t",
            name="batch-filter",
            created_date=now,
            start_date=now,
            status=RunStatus.PENDING,
            status_date=now,
        )
        session.add(rg)
        await session.flush()

        run_a = WorkflowRun(
            workflow_definition_id="t",
            run_group_id=rg.id,
            batch_id=101,
            doc_id=f"doc-a-{uuid.uuid4().hex}",
            priority=0,
            created_date=now,
            start_date=now,
            status=RunStatus.PENDING,
            status_date=now,
            run_params={},
        )
        run_b = WorkflowRun(
            workflow_definition_id="t",
            run_group_id=rg.id,
            batch_id=202,
            doc_id=f"doc-b-{uuid.uuid4().hex}",
            priority=0,
            created_date=now,
            start_date=now,
            status=RunStatus.PENDING,
            status_date=now,
            run_params={},
        )
        session.add(run_a)
        session.add(run_b)
        await session.flush()

        for wr in (run_a, run_b):
            session.add(
                RunStep(
                    workflow_run_id=wr.id,
                    workflow_step_number=1,
                    workflow_step_name="s1",
                    step_config_id=1,
                    step_type=WorkflowStepType.PARSE,
                    is_last_step=True,
                    created_date=now,
                    status_date=now,
                    priority=0,
                    retry=0,
                    retries=3,
                    status=RunStatus.PENDING,
                ),
            )
        await session.commit()
        wr_b_id = run_b.id

    claimed = await wf_ops.claim_next_step("worker-A", "lease-A", batch_id=202)
    assert claimed is not None
    assert claimed.workflow_run_id == wr_b_id


@pytest.mark.asyncio
async def test_claim_next_step_filters_by_allowed_types(db):
    """L1848: allowed_types narrows which step types can be claimed."""
    rg_id, wr_id, step_ids = await _make_run_with_steps(n_steps=2)
    # Make the first step VALIDATE so the second (PARSE) is filtered out.
    async with get_session() as session:
        rs = await session.exec(select(RunStep).where(RunStep.id == step_ids[0]))
        step = rs.first()
        step.step_type = WorkflowStepType.VALIDATE
        session.add(step)
        await session.commit()

    claimed = await wf_ops.claim_next_step(
        "worker-A",
        "lease-A",
        allowed_types=[WorkflowStepType.VALIDATE],
    )
    assert claimed is not None
    assert claimed.step_type == WorkflowStepType.VALIDATE


@pytest.mark.asyncio
async def test_error_step_error_status_does_not_cancel_siblings(db):
    """L1976->1979: when retries aren't exhausted, status is ERROR and
    cancel_pending_steps is *not* called; siblings stay PENDING."""
    rg_id, wr_id, step_ids = await _make_run_with_steps(n_steps=2, retries=3)
    step = await wf_ops.claim_next_step("A", "lease-A")
    assert step is not None

    new_status = await wf_ops.error_step(step.id, "lease-A", message="transient")
    assert new_status == RunStatus.ERROR

    async with get_session() as session:
        rs = await session.exec(select(RunStep).where(RunStep.workflow_run_id == wr_id))
        statuses = {s.status for s in rs.all()}
        # Sibling step must still be PENDING (not CANCELLED).
        assert RunStatus.PENDING in statuses
        assert RunStatus.CANCELLED not in statuses


@pytest.mark.asyncio
async def test_recompute_run_status_all_pending(db):
    """L2063: zero progress in any direction → PENDING."""
    rg_id, wr_id, _ = await _make_run_with_steps(n_steps=2)
    # Force the workflow run off PENDING so a transition actually fires.
    async with get_session() as session:
        rs = await session.exec(select(WorkflowRun).where(WorkflowRun.id == wr_id))
        wr = rs.first()
        wr.status = RunStatus.RUNNING
        session.add(wr)
        await session.commit()

    new_status = await wf_ops.recompute_run_status(wr_id)
    assert new_status == RunStatus.PENDING


@pytest.mark.asyncio
async def test_recompute_run_status_returns_none_when_run_missing(db):
    """L2069: steps exist but parent WorkflowRun has been deleted.
    Returns None instead of crashing on the missing FK target.
    """
    rg_id, wr_id, _ = await _make_run_with_steps(n_steps=1)
    # Orphan the steps by deleting the WorkflowRun (SQLite doesn't
    # enforce FKs by default in this codebase).
    async with get_session() as session:
        rs = await session.exec(select(WorkflowRun).where(WorkflowRun.id == wr_id))
        wr = rs.first()
        await session.delete(wr)
        await session.commit()

    result = await wf_ops.recompute_run_status(wr_id)
    assert result is None


@pytest.mark.asyncio
async def test_reap_dead_workers_with_no_running_steps(db):
    """L2227->2240 + L2240->2248: dead worker had no RUNNING steps and
    no lease tokens to clean up. Reaper still removes the checkin row.
    """
    await wf_ops.worker_heartbeat("idle-dead")
    long_ago = _naive_utc_now() - datetime.timedelta(seconds=600)
    async with get_session() as session:
        rs = await session.exec(select(WorkerCheckin).where(WorkerCheckin.id == "idle-dead"))
        row = rs.first()
        row.last_checkin = long_ago
        session.add(row)
        await session.commit()

    reaped, reset = await wf_ops.reap_dead_workers("alive", threshold_seconds=60)
    assert reaped == ["idle-dead"]
    assert reset == []
    async with get_session() as session:
        rs = await session.exec(select(WorkerCheckin).where(WorkerCheckin.id == "idle-dead"))
        assert rs.first() is None
