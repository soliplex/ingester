"""Tests for the new wf claim/lease/resource-lock/reaper APIs."""

import datetime
import uuid

import pytest

from soliplex.ingester.lib.models import ResourceLock
from soliplex.ingester.lib.models import ResourceLockKind
from soliplex.ingester.lib.models import RunGroup
from soliplex.ingester.lib.models import RunStatus
from soliplex.ingester.lib.models import RunStep
from soliplex.ingester.lib.models import WorkerCheckin
from soliplex.ingester.lib.models import WorkflowRun
from soliplex.ingester.lib.models import WorkflowStepType
from soliplex.ingester.lib.models import get_session
from soliplex.ingester.lib.wf import operations as ops


async def _scaffold(
    n_runs: int = 1,
    steps_per_run: int = 2,
    resource_keys: list[str | None] | None = None,
    priorities: list[int] | None = None,
) -> tuple[int, list[int], list[list[int]]]:
    """Insert a run group, *n_runs* workflow runs, and *steps_per_run*
    PENDING run steps each. Returns (run_group_id, run_ids,
    step_ids[run_idx][step_idx]).

    *resource_keys* lets callers stamp a resource_key on every step
    of a particular run (1 entry per run).
    """
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
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
        run_ids: list[int] = []
        step_ids: list[list[int]] = []
        for i in range(n_runs):
            wr = WorkflowRun(
                workflow_definition_id="test",
                run_group_id=rg.id,
                batch_id=1,
                doc_id=f"doc-{uuid.uuid4().hex}",
                priority=(priorities[i] if priorities else 0),
                created_date=now,
                start_date=now,
                status=RunStatus.PENDING,
                status_date=now,
                run_params={},
            )
            session.add(wr)
            await session.flush()
            run_ids.append(wr.id)
            ids: list[int] = []
            rk = resource_keys[i] if resource_keys else None
            for j in range(steps_per_run):
                rs = RunStep(
                    workflow_run_id=wr.id,
                    workflow_step_number=j + 1,
                    workflow_step_name=f"step-{j + 1}",
                    step_config_id=1,
                    step_type=WorkflowStepType.PARSE,
                    is_last_step=(j == steps_per_run - 1),
                    created_date=now,
                    status_date=now,
                    priority=(priorities[i] if priorities else 0),
                    retry=0,
                    retries=3,
                    status=RunStatus.PENDING,
                    resource_key=rk,
                )
                session.add(rs)
                await session.flush()
                ids.append(rs.id)
            step_ids.append(ids)
        rg_id_out = rg.id
        await session.commit()
        return rg_id_out, run_ids, step_ids


@pytest.mark.asyncio
async def test_claim_returns_first_step(db):
    rg_id, run_ids, step_ids = await _scaffold(n_runs=1, steps_per_run=2)
    step = await ops.claim_next_step("worker-A", "lease-1")
    assert step is not None
    # First step (workflow_step_number=1) gets claimed before second.
    assert step.id == step_ids[0][0]
    assert step.status == RunStatus.RUNNING
    assert step.worker_id == "worker-A"
    assert step.lease_token == "lease-1"


@pytest.mark.asyncio
async def test_claim_returns_none_when_no_work(db):
    step = await ops.claim_next_step("worker-A", "lease-1")
    assert step is None


@pytest.mark.asyncio
async def test_claim_skips_step_with_locked_resource(db):
    rk = "rag:/tmp/lockedDB"
    rg_id, run_ids, step_ids = await _scaffold(
        n_runs=2,
        steps_per_run=1,
        resource_keys=[rk, None],
    )
    # Lock the resource externally before any worker can claim.
    got = await ops.acquire_resource_lock(
        rk,
        holder_id="external",
        holder_kind=ResourceLockKind.WEB,
        ttl_seconds=60,
    )
    assert got is True

    # The step on the locked DB must be skipped; the unlocked one is
    # claimed instead.
    step = await ops.claim_next_step("worker-A", "lease-1")
    assert step is not None
    assert step.id == step_ids[1][0]


@pytest.mark.asyncio
async def test_claim_skips_running_resource_key_without_lock_row(db):
    """Regression: closes the race window between a claim
    transaction committing (step → RUNNING) and the worker's
    separate acquire_resource_lock transaction committing. The
    claim filter must look at RUNNING-step resource_keys, not
    just rows in ``resourcelock``.
    """
    rk = "rag:/tmp/sharedDB"
    rg_id, run_ids, step_ids = await _scaffold(
        n_runs=2,
        steps_per_run=1,
        resource_keys=[rk, rk],
    )

    # First worker claims; no ResourceLock row is inserted by the
    # test (we're simulating the race window where claim has
    # committed RUNNING but the worker hasn't yet run
    # ``acquire_resource_lock``).
    a = await ops.claim_next_step("A", "lease-A")
    assert a is not None
    assert a.id == step_ids[0][0]
    assert a.status == RunStatus.RUNNING

    # Second worker's claim must skip the other step that shares
    # the same resource_key, even though no ResourceLock row
    # exists yet.
    b = await ops.claim_next_step("B", "lease-B")
    assert b is None


@pytest.mark.asyncio
async def test_claim_picks_step_with_different_resource_key(db):
    """Distinct resource_keys can be claimed in parallel — the
    in-flight filter is per-key, not per-pool."""
    rk1 = "rag:/tmp/dbA"
    rk2 = "rag:/tmp/dbB"
    rg_id, run_ids, step_ids = await _scaffold(
        n_runs=2,
        steps_per_run=1,
        resource_keys=[rk1, rk2],
    )

    a = await ops.claim_next_step("A", "lease-A")
    assert a is not None
    assert a.id == step_ids[0][0]
    assert a.resource_key == rk1

    # Second worker can still claim the other step because its
    # resource_key is different.
    b = await ops.claim_next_step("B", "lease-B")
    assert b is not None
    assert b.id == step_ids[1][0]
    assert b.resource_key == rk2


@pytest.mark.asyncio
async def test_two_workers_never_claim_same_step(db):
    rg_id, run_ids, step_ids = await _scaffold(n_runs=1, steps_per_run=1)
    # Issue claims sequentially (SQLite serializes writers anyway,
    # but the assertion is just about correctness of the contract).
    a = await ops.claim_next_step("A", "lease-A")
    b = await ops.claim_next_step("B", "lease-B")
    assert a is not None
    assert b is None


@pytest.mark.asyncio
async def test_complete_step_only_with_matching_lease(db):
    rg_id, run_ids, step_ids = await _scaffold(n_runs=1, steps_per_run=1)
    step = await ops.claim_next_step("A", "lease-A")
    assert step is not None

    # Wrong lease → no-op, step stays RUNNING.
    ok = await ops.complete_step(step.id, "lease-WRONG")
    assert ok is False

    async with get_session() as session:
        from sqlmodel import select

        rs = await session.exec(select(RunStep).where(RunStep.id == step.id))
        s = rs.first()
        assert s.status == RunStatus.RUNNING

    # Correct lease succeeds; lease_token is cleared.
    ok = await ops.complete_step(step.id, "lease-A")
    assert ok is True
    async with get_session() as session:
        from sqlmodel import select

        rs = await session.exec(select(RunStep).where(RunStep.id == step.id))
        s = rs.first()
        assert s.status == RunStatus.COMPLETED
        assert s.lease_token is None


@pytest.mark.asyncio
async def test_error_step_increments_retry_and_elevates_to_failed(db):
    rg_id, run_ids, step_ids = await _scaffold(n_runs=1, steps_per_run=1)
    # Crank retries down so we exhaust quickly.
    async with get_session() as session:
        from sqlmodel import select

        rs = await session.exec(select(RunStep).where(RunStep.id == step_ids[0][0]))
        s = rs.first()
        s.retries = 1
        session.add(s)
        await session.commit()

    step = await ops.claim_next_step("A", "lease-1")
    new_status = await ops.error_step(step.id, "lease-1", message="boom")
    assert new_status == RunStatus.FAILED


@pytest.mark.asyncio
async def test_release_step_returns_to_pending(db):
    rg_id, run_ids, step_ids = await _scaffold(n_runs=1, steps_per_run=1)
    step = await ops.claim_next_step("A", "lease-A")
    assert step is not None

    released = await ops.release_step(step.id, "lease-A")
    assert released is True

    async with get_session() as session:
        from sqlmodel import select

        rs = await session.exec(select(RunStep).where(RunStep.id == step.id))
        s = rs.first()
        assert s.status == RunStatus.PENDING
        assert s.worker_id is None
        assert s.lease_token is None

    # Re-claimable immediately by a fresh worker.
    step2 = await ops.claim_next_step("B", "lease-B")
    assert step2 is not None
    assert step2.id == step.id


@pytest.mark.asyncio
async def test_reap_dead_workers_excludes_self(db):
    """Self-reaping race regression test: a worker's own checkin row
    is never considered dead, even if its last_checkin is past the
    threshold."""
    rg_id, run_ids, step_ids = await _scaffold(n_runs=1, steps_per_run=1)
    step = await ops.claim_next_step("A", "lease-A")
    assert step is not None

    # Stamp our own checkin in the past — well past the threshold.
    long_ago = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(seconds=600)
    async with get_session() as session:
        from sqlmodel import select

        rs = await session.exec(select(WorkerCheckin).where(WorkerCheckin.id == "A"))
        existing = rs.first()
        if existing is None:
            session.add(WorkerCheckin(id="A", first_checkin=long_ago, last_checkin=long_ago))
        else:
            existing.last_checkin = long_ago
            session.add(existing)
        await session.commit()

    reaped, reset = await ops.reap_dead_workers("A", threshold_seconds=60)
    assert reaped == []
    assert reset == []


@pytest.mark.asyncio
async def test_reap_dead_workers_resets_orphaned_steps(db):
    rg_id, run_ids, step_ids = await _scaffold(n_runs=1, steps_per_run=1)
    # Register the dead worker first so the reaper has a checkin
    # row to consider expired.
    await ops.worker_heartbeat("dead-worker")
    step = await ops.claim_next_step("dead-worker", "lease-dead")
    assert step is not None

    long_ago = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(seconds=600)
    async with get_session() as session:
        from sqlmodel import select

        rs = await session.exec(select(WorkerCheckin).where(WorkerCheckin.id == "dead-worker"))
        existing = rs.first()
        existing.last_checkin = long_ago
        session.add(existing)
        await session.commit()

    reaped, reset = await ops.reap_dead_workers("live-worker", threshold_seconds=60)
    assert reaped == ["dead-worker"]
    assert reset == [step.id]

    async with get_session() as session:
        from sqlmodel import select

        rs = await session.exec(select(RunStep).where(RunStep.id == step.id))
        s = rs.first()
        assert s.status == RunStatus.PENDING
        assert s.worker_id is None
        assert s.lease_token is None


@pytest.mark.asyncio
async def test_lease_lost_after_reap_blocks_stale_completion(db):
    """Combined integration: a stale worker that wakes up after
    being reaped must not be able to mark its old step COMPLETED on
    top of a fresh claimant."""
    rg_id, run_ids, step_ids = await _scaffold(n_runs=1, steps_per_run=1)
    await ops.worker_heartbeat("worker-stale")
    stale_step = await ops.claim_next_step("worker-stale", "lease-stale")
    assert stale_step is not None

    # Simulate the reaper running while worker-stale was still
    # processing the step.
    long_ago = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(seconds=600)
    async with get_session() as session:
        from sqlmodel import select

        rs = await session.exec(select(WorkerCheckin).where(WorkerCheckin.id == "worker-stale"))
        existing = rs.first()
        existing.last_checkin = long_ago
        session.add(existing)
        await session.commit()
    await ops.reap_dead_workers("worker-fresh", threshold_seconds=60)

    # Fresh worker re-claims.
    fresh = await ops.claim_next_step("worker-fresh", "lease-fresh")
    assert fresh is not None
    assert fresh.id == stale_step.id

    # Stale worker tries to complete with its now-invalid lease — must fail.
    ok = await ops.complete_step(stale_step.id, "lease-stale")
    assert ok is False


@pytest.mark.asyncio
async def test_resource_lock_acquire_release_refresh(db):
    key = "rag:/tmp/foo"
    a = await ops.acquire_resource_lock(key, "holder-A", ResourceLockKind.WORKER, ttl_seconds=60)
    assert a is True

    # Second acquire fails.
    b = await ops.acquire_resource_lock(key, "holder-B", ResourceLockKind.WORKER, ttl_seconds=60)
    assert b is False

    # Refresh by holder works; refresh by non-holder is a no-op.
    assert await ops.refresh_resource_lock(key, "holder-A", ttl_seconds=120) is True
    assert await ops.refresh_resource_lock(key, "holder-B", ttl_seconds=120) is False

    # Release by holder works.
    assert await ops.release_resource_lock(key, "holder-A") is True
    # Now another holder can acquire.
    assert await ops.acquire_resource_lock(key, "holder-B", ResourceLockKind.WORKER, ttl_seconds=60) is True


@pytest.mark.asyncio
async def test_resource_lock_sweep_expired(db):
    key = "rag:/tmp/foo"
    # Hand-roll an expired row.
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    expired = now - datetime.timedelta(seconds=60)
    async with get_session() as session:
        session.add(
            ResourceLock(
                resource_key=key,
                holder_id="dead",
                holder_kind=ResourceLockKind.WORKER,
                acquired_at=expired,
                expires_at=expired,
                holder_meta={},
            ),
        )
        await session.commit()

    swept = await ops.sweep_expired_resource_locks()
    assert swept == 1


@pytest.mark.asyncio
async def test_recompute_run_status_failed_dominates(db):
    rg_id, run_ids, step_ids = await _scaffold(n_runs=1, steps_per_run=2)
    async with get_session() as session:
        from sqlmodel import select

        rs = await session.exec(select(RunStep).where(RunStep.id == step_ids[0][0]))
        s = rs.first()
        s.status = RunStatus.FAILED
        session.add(s)
        await session.commit()

    new_status = await ops.recompute_run_status(run_ids[0])
    assert new_status == RunStatus.FAILED


@pytest.mark.asyncio
async def test_recompute_run_status_completed_when_all_done(db):
    rg_id, run_ids, step_ids = await _scaffold(n_runs=1, steps_per_run=2)
    async with get_session() as session:
        from sqlmodel import select

        for sid in step_ids[0]:
            rs = await session.exec(select(RunStep).where(RunStep.id == sid))
            s = rs.first()
            s.status = RunStatus.COMPLETED
            session.add(s)
        await session.commit()

    new_status = await ops.recompute_run_status(run_ids[0])
    assert new_status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_try_complete_run_group_failed_when_any_step_failed(db):
    rg_id, run_ids, step_ids = await _scaffold(n_runs=1, steps_per_run=2)
    async with get_session() as session:
        from sqlmodel import select

        for i, sid in enumerate(step_ids[0]):
            rs = await session.exec(select(RunStep).where(RunStep.id == sid))
            s = rs.first()
            s.status = RunStatus.FAILED if i == 0 else RunStatus.COMPLETED
            session.add(s)
        await session.commit()

    new_status = await ops.try_complete_run_group(rg_id)
    assert new_status == RunStatus.FAILED

    # Calling again should return None (already terminal).
    again = await ops.try_complete_run_group(rg_id)
    assert again is None


@pytest.mark.asyncio
async def test_try_complete_run_group_pending_returns_none(db):
    rg_id, run_ids, step_ids = await _scaffold(n_runs=1, steps_per_run=2)
    new_status = await ops.try_complete_run_group(rg_id)
    assert new_status is None


@pytest.mark.asyncio
async def test_resource_key_is_set_for_save_to_rag_steps_at_creation(db):
    """create_workflow_run stamps resource_key on STORE-type steps
    using the param set's storage path. We can't run the full
    machinery here without the workflow registry, so we just check
    the helper that resolves the key."""
    # The function returns None when param_id is None or the param
    # set has no STORE config — those are the trivial branches.
    assert await ops._rag_resource_key_for_param(None) is None
