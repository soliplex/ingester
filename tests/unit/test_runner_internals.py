"""Comprehensive coverage for soliplex.ingester.lib.wf.runner.

Companion to ``test_runner.py`` (which tests the pure state-machine
helpers). This file exercises:

* :class:`LoggingMetrics`
* :class:`LifecycleEventEnvelope` / :class:`LifecycleBus` (subscribe,
  publish fan-out, drain, error swallowing).
* :class:`WorkerConfig` defaults and overrides.
* :class:`Worker` — construction, ``id`` / ``lifecycle`` accessors,
  ``_allowed_types_for`` partitioning, the success / lease-lost /
  resource-lock-race / handler-error / cancelled / error_step-raises
  paths through ``_run_step``, and the heartbeat / reaper / sweeper
  loops (one iteration each, with stop signalled).
* :func:`_run_workflow_lifecycle_handlers` — None handlers, missing
  event, success, handler raises, history-update raises.
* The legacy module shims ``start_worker`` / ``stop_worker`` /
  ``get_worker_id`` / ``get_runnable_steps`` / ``do_state_transition``.
* :func:`build_coro` / :func:`build_step_coro` parameter binding.

We mock :mod:`operations` for everything that isn't directly under
test, so each test stays focused on runner.py's own logic.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import logging
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from soliplex.ingester.lib.models import LifeCycleEvent
from soliplex.ingester.lib.models import RunStatus
from soliplex.ingester.lib.models import WorkflowStepType
from soliplex.ingester.lib.wf import runner
from soliplex.ingester.lib.wf.runner import LifecycleBus
from soliplex.ingester.lib.wf.runner import LifecycleEventEnvelope
from soliplex.ingester.lib.wf.runner import LoggingMetrics
from soliplex.ingester.lib.wf.runner import Worker
from soliplex.ingester.lib.wf.runner import WorkerConfig
from soliplex.ingester.lib.wf.runner import WorkflowException
from soliplex.ingester.lib.wf.runner import _InFlight
from soliplex.ingester.lib.wf.runner import _run_workflow_lifecycle_handlers
from soliplex.ingester.lib.wf.runner import build_coro
from soliplex.ingester.lib.wf.runner import build_step_coro
from soliplex.ingester.lib.wf.runner import do_state_transition
from soliplex.ingester.lib.wf.runner import get_runnable_steps
from soliplex.ingester.lib.wf.runner import get_worker_id
from soliplex.ingester.lib.wf.runner import start_worker
from soliplex.ingester.lib.wf.runner import stop_worker

OPS = "soliplex.ingester.lib.wf.runner.operations"


def _patch_op(name: str, **kwargs):
    """Shorthand for ``patch("soliplex.ingester.lib.wf.runner.operations.<name>", new_callable=AsyncMock, ...)``."""
    return patch(f"{OPS}.{name}", new_callable=AsyncMock, **kwargs)


@contextlib.contextmanager
def _patch_runtime_lookups(rt):
    """Patch the per-step lookup calls (``get_workflow_run``,
    ``get_run_group``, ``get_workflow_definition``,
    ``get_step_config_by_id``, ``get_batch``) to return *rt*'s values."""
    with (
        _patch_op("get_workflow_run", return_value=rt["workflow_run"]),
        _patch_op("get_run_group", return_value=rt["run_group"]),
        _patch_op("get_workflow_definition", return_value=rt["workflow_def"]),
        _patch_op("get_step_config_by_id", return_value=rt["step_config"]),
        _patch_op("get_batch", return_value=rt["batch"]),
    ):
        yield


@contextlib.contextmanager
def _patch_loop_ops():
    """Patch the background-loop ops to no-ops so a started Worker
    can be stopped without touching a real DB."""
    with (
        _patch_op("worker_heartbeat"),
        _patch_op("claim_next_step", return_value=None),
        _patch_op("reap_dead_workers", return_value=([], [])),
        _patch_op("sweep_expired_resource_locks", return_value=0),
        _patch_op("delete_worker_checkin"),
    ):
        yield


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_default_worker():
    """The module-level shims keep a process-wide default Worker.
    Reset it between tests so leakage can't influence ordering."""
    runner._default_worker = None
    yield
    runner._default_worker = None


def _make_envelope(event: LifeCycleEvent = LifeCycleEvent.STEP_START) -> LifecycleEventEnvelope:
    workflow_def = MagicMock()
    workflow_def.name = "wf"
    workflow_def.lifecycle_events = None
    run_step = MagicMock()
    run_step.id = 7
    run_step.workflow_step_number = 1
    run_step.workflow_step_name = "first"
    run_step.is_last_step = False
    run_step.priority = 0
    run_step.retry = 0
    run_step.retries = 1
    run_step.workflow_run_id = 11
    run_step.step_config_id = 1
    run_step.step_type = WorkflowStepType.PARSE
    run_step.resource_key = None
    workflow_run = MagicMock()
    workflow_run.id = 11
    workflow_run.run_group_id = 21
    workflow_run.workflow_definition_id = "wf"
    workflow_run.batch_id = 31
    workflow_run.doc_id = "doc"
    workflow_run.run_params = {}
    run_group = MagicMock()
    run_group.id = 21
    return LifecycleEventEnvelope(event, workflow_def, run_step, workflow_run, run_group)


# ---------------------------------------------------------------------
# LoggingMetrics
# ---------------------------------------------------------------------


class TestLoggingMetrics:
    def test_incr_logs_at_debug(self, caplog):
        m = LoggingMetrics()
        with caplog.at_level(logging.DEBUG, logger="soliplex.ingester.lib.wf.runner"):
            m.incr("foo", 5, label="x")
        assert any("metric incr foo=5" in r.message for r in caplog.records)

    def test_observe_logs_at_debug(self, caplog):
        m = LoggingMetrics()
        with caplog.at_level(logging.DEBUG, logger="soliplex.ingester.lib.wf.runner"):
            m.observe("dur", 1.5, label="y")
        assert any("metric observe dur=" in r.message for r in caplog.records)


# ---------------------------------------------------------------------
# LifecycleEventEnvelope
# ---------------------------------------------------------------------


class TestLifecycleEventEnvelope:
    def test_envelope_is_frozen_dataclass(self):
        env = _make_envelope()
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.event = LifeCycleEvent.STEP_END  # type: ignore[misc]


# ---------------------------------------------------------------------
# LifecycleBus
# ---------------------------------------------------------------------


class TestLifecycleBus:
    @pytest.mark.asyncio
    async def test_subscribe_and_publish_fans_out(self):
        bus = LifecycleBus()
        seen: list[LifeCycleEvent] = []

        async def sub(env: LifecycleEventEnvelope) -> None:
            seen.append(env.event)

        bus.subscribe(sub)
        env = _make_envelope(LifeCycleEvent.STEP_END)
        await bus.publish(env)
        await bus.drain(timeout=2.0)
        assert seen == [LifeCycleEvent.STEP_END]

    @pytest.mark.asyncio
    async def test_publish_runs_each_subscriber(self):
        bus = LifecycleBus()
        calls = {"a": 0, "b": 0}

        async def a(env):
            calls["a"] += 1

        async def b(env):
            calls["b"] += 1

        bus.subscribe(a)
        bus.subscribe(b)
        await bus.publish(_make_envelope())
        await bus.publish(_make_envelope())
        await bus.drain()
        assert calls == {"a": 2, "b": 2}

    @pytest.mark.asyncio
    async def test_drain_with_no_pending_returns_immediately(self):
        bus = LifecycleBus()
        # Should not hang or raise.
        await bus.drain(timeout=0.05)

    @pytest.mark.asyncio
    async def test_subscriber_exception_is_swallowed_and_logged(self, caplog):
        bus = LifecycleBus()

        async def boom(env):
            raise RuntimeError("kaboom")

        async def ok(env):
            pass

        bus.subscribe(boom)
        bus.subscribe(ok)
        with caplog.at_level(logging.ERROR, logger="soliplex.ingester.lib.wf.runner"):
            await bus.publish(_make_envelope())
            await bus.drain()
        assert any("lifecycle subscriber failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------
# WorkerConfig
# ---------------------------------------------------------------------


class TestWorkerConfig:
    def test_required_consumers_field(self):
        cfg = WorkerConfig(consumers={"parse": 2})
        assert cfg.consumers == {"parse": 2}

    def test_defaults(self):
        cfg = WorkerConfig(consumers={"*": 1})
        assert cfg.poll_interval == 1.0
        assert cfg.poll_backoff_max == 3.0
        assert cfg.checkin_interval is None
        assert cfg.checkin_timeout is None
        assert cfg.resource_lock_ttl == 300


# ---------------------------------------------------------------------
# Worker construction + accessors + _allowed_types_for
# ---------------------------------------------------------------------


class TestWorkerConstruction:
    def test_default_config_uses_settings_worker_task_count(self):
        w = Worker()
        # Either {"*": N} from settings, or whatever the operator
        # configured. We just assert the catch-all is present.
        assert "*" in w._config.consumers
        assert w._config.consumers["*"] >= 1

    def test_empty_consumers_dict_is_filled_with_default(self):
        w = Worker(config=WorkerConfig(consumers={}))
        assert "*" in w._config.consumers

    def test_explicit_consumers_preserved(self):
        w = Worker(config=WorkerConfig(consumers={"parse": 3, "*": 1}))
        assert w._config.consumers == {"parse": 3, "*": 1}

    def test_id_property_returns_worker_uuid(self):
        w = Worker(config=WorkerConfig(consumers={"*": 1}))
        assert isinstance(w.id, str)
        # uuid4 hex form has 36 chars including dashes.
        assert len(w.id) == 36

    def test_lifecycle_property_returns_bus(self):
        w = Worker(config=WorkerConfig(consumers={"*": 1}))
        assert isinstance(w.lifecycle, LifecycleBus)

    def test_two_workers_have_distinct_ids(self):
        a = Worker(config=WorkerConfig(consumers={"*": 1}))
        b = Worker(config=WorkerConfig(consumers={"*": 1}))
        assert a.id != b.id

    def test_metrics_default_is_logging_metrics(self):
        w = Worker(config=WorkerConfig(consumers={"*": 1}))
        assert isinstance(w._metrics, LoggingMetrics)

    def test_custom_metrics_used(self):
        m = MagicMock()
        w = Worker(config=WorkerConfig(consumers={"*": 1}), metrics=m)
        assert w._metrics is m


class TestAllowedTypesFor:
    def test_named_pool_returns_singleton(self):
        w = Worker(config=WorkerConfig(consumers={"parse": 2, "*": 1}))
        assert w._allowed_types_for("parse") == [WorkflowStepType.PARSE]

    def test_star_pool_with_no_named_pools_returns_none(self):
        w = Worker(config=WorkerConfig(consumers={"*": 1}))
        assert w._allowed_types_for("*") is None

    def test_star_pool_excludes_named_step_types(self):
        w = Worker(config=WorkerConfig(consumers={"parse": 2, "store": 1, "*": 1}))
        excluded = w._allowed_types_for("*")
        assert excluded is not None
        assert WorkflowStepType.PARSE not in excluded
        assert WorkflowStepType.STORE not in excluded
        # Some other step type still in the catch-all.
        assert WorkflowStepType.CHUNK in excluded


# ---------------------------------------------------------------------
# Worker.start / stop
# ---------------------------------------------------------------------


class TestWorkerStartStop:
    @pytest.mark.asyncio
    async def test_start_schedules_tasks_and_heartbeats(self):
        w = Worker(config=WorkerConfig(consumers={"*": 1}))
        with _patch_loop_ops(), _patch_op("worker_heartbeat") as hb:
            await w.start()
            try:
                # Three background loops + 1 consumer.
                assert len(w._tasks) == 4
                hb.assert_awaited()
            finally:
                await w.stop(timeout=2.0)

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        w = Worker(config=WorkerConfig(consumers={"*": 1}))
        with _patch_loop_ops():
            await w.start()
            initial = len(w._tasks)
            await w.start()  # should be a no-op
            try:
                assert len(w._tasks) == initial
            finally:
                await w.stop(timeout=2.0)

    @pytest.mark.asyncio
    async def test_stop_when_never_started_is_noop(self):
        w = Worker(config=WorkerConfig(consumers={"*": 1}))
        # Should not even touch operations.
        await w.stop(timeout=2.0)

    @pytest.mark.asyncio
    async def test_stop_drains_tasks_and_clears_state(self):
        w = Worker(config=WorkerConfig(consumers={"*": 1}))
        with _patch_loop_ops(), _patch_op("delete_worker_checkin") as delete_hb:
            await w.start()
            await w.stop(timeout=2.0)
            assert w._tasks == []
            delete_hb.assert_awaited_with(w._worker_id)

    @pytest.mark.asyncio
    async def test_stop_swallows_delete_checkin_error(self, caplog):
        w = Worker(config=WorkerConfig(consumers={"*": 1}))
        with (
            _patch_loop_ops(),
            _patch_op("delete_worker_checkin", side_effect=RuntimeError("db down")),
        ):
            await w.start()
            with caplog.at_level(logging.ERROR, logger="soliplex.ingester.lib.wf.runner"):
                await w.stop(timeout=2.0)
        assert any("failed to delete checkin row" in r.message for r in caplog.records)


# ---------------------------------------------------------------------
# Worker._run_step (the bulk of the orchestration)
# ---------------------------------------------------------------------


def _build_runtime_mocks(
    *,
    workflow_def_lifecycle_events=None,
    handler_returns="ok",
    resource_key=None,
    is_last_step=False,
    workflow_step_number=1,
    retry=0,
    retries=1,
):
    """Set up the patched ``operations`` calls that ``_run_step``
    relies on. Returns a dict of {name: mock} for assertions."""
    workflow_run = MagicMock()
    workflow_run.id = 11
    workflow_run.run_group_id = 21
    workflow_run.workflow_definition_id = "wf"
    workflow_run.batch_id = 31
    workflow_run.doc_id = "doc"
    workflow_run.run_params = {}
    run_group = MagicMock()
    run_group.id = 21
    workflow_def = MagicMock()
    workflow_def.name = "wf"
    handler = MagicMock(method=AsyncMock(return_value=handler_returns), parameters={})
    workflow_def.item_steps = {WorkflowStepType.PARSE: handler}
    workflow_def.lifecycle_events = workflow_def_lifecycle_events
    step_config = MagicMock()
    step_config.step_type = WorkflowStepType.PARSE
    batch = MagicMock()
    batch.source = "src"
    batch.id = 31

    return {
        "workflow_run": workflow_run,
        "run_group": run_group,
        "workflow_def": workflow_def,
        "step_config": step_config,
        "batch": batch,
    }


def _make_run_step(
    *,
    step_id=1,
    workflow_run_id=11,
    workflow_step_number=1,
    is_last_step=False,
    resource_key=None,
    retry=0,
    retries=1,
):
    rs = MagicMock()
    rs.id = step_id
    rs.workflow_run_id = workflow_run_id
    rs.workflow_step_number = workflow_step_number
    rs.workflow_step_name = "step"
    rs.priority = 0
    rs.retry = retry
    rs.retries = retries
    rs.is_last_step = is_last_step
    rs.resource_key = resource_key
    rs.step_type = WorkflowStepType.PARSE
    rs.step_config_id = 1
    return rs


@pytest.mark.asyncio
async def test_run_step_success_publishes_step_start_and_end():
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    rt = _build_runtime_mocks()
    rs = _make_run_step()
    events: list[LifeCycleEvent] = []

    async def collect(env):
        events.append(env.event)

    w.lifecycle.subscribe(collect)
    with (
        _patch_runtime_lookups(rt),
        _patch_op("complete_step", return_value=True) as complete,
        _patch_op("recompute_run_status") as recompute,
    ):
        await w._run_step(rs, "lease-1", coro_id=0)
        await w.lifecycle.drain(timeout=2.0)

    assert LifeCycleEvent.STEP_START in events
    assert LifeCycleEvent.ITEM_START in events  # workflow_step_number=1
    assert LifeCycleEvent.STEP_END in events
    complete.assert_awaited_once()
    recompute.assert_awaited_once_with(11)
    assert rs.id not in w._inflight


@pytest.mark.asyncio
async def test_run_step_last_step_publishes_item_end():
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    rt = _build_runtime_mocks()
    rs = _make_run_step(workflow_step_number=3, is_last_step=True)
    events: list[LifeCycleEvent] = []

    async def collect(env):
        events.append(env.event)

    w.lifecycle.subscribe(collect)
    with (
        _patch_runtime_lookups(rt),
        _patch_op("complete_step", return_value=True),
        _patch_op("recompute_run_status"),
    ):
        await w._run_step(rs, "lease-1", 0)
        await w.lifecycle.drain(timeout=2.0)

    assert LifeCycleEvent.ITEM_START not in events  # not first step
    assert LifeCycleEvent.ITEM_END in events


@pytest.mark.asyncio
async def test_run_step_acquires_resource_lock_when_present():
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    rt = _build_runtime_mocks()
    rs = _make_run_step(resource_key="rag:/tmp/db")
    with (
        _patch_runtime_lookups(rt),
        _patch_op("acquire_resource_lock", return_value=True) as acquire,
        _patch_op("complete_step", return_value=True),
        _patch_op("recompute_run_status"),
    ):
        await w._run_step(rs, "lease-1", 0)
        acquire.assert_awaited_once()
        kwargs = acquire.await_args.kwargs
        assert kwargs["holder_id"] == "lease-1"
        assert kwargs["step_id"] == 1


@pytest.mark.asyncio
async def test_run_step_releases_when_resource_lock_lost(caplog):
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    rt = _build_runtime_mocks()
    rs = _make_run_step(resource_key="rag:/tmp/db")
    with (
        _patch_runtime_lookups(rt),
        _patch_op("acquire_resource_lock", return_value=False),
        _patch_op("release_step", return_value=True) as release,
        _patch_op("complete_step") as complete,
    ):
        with caplog.at_level(logging.WARNING, logger="soliplex.ingester.lib.wf.runner"):
            raced = await w._run_step(rs, "lease-1", 0)
        release.assert_awaited_once_with(1, "lease-1")
        complete.assert_not_called()
    assert raced is True
    assert any("race on resource_key" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_step_lease_lost_on_complete_returns_quietly(caplog):
    w = Worker(config=WorkerConfig(consumers={"*": 1}), metrics=MagicMock())
    rt = _build_runtime_mocks()
    rs = _make_run_step()
    with (
        _patch_runtime_lookups(rt),
        _patch_op("complete_step", return_value=False),
        _patch_op("recompute_run_status") as recompute,
    ):
        with caplog.at_level(logging.WARNING, logger="soliplex.ingester.lib.wf.runner"):
            await w._run_step(rs, "lease-1", 0)
    w._metrics.incr.assert_any_call("lease_lost", phase="complete")
    # Run-status recompute is NOT called when the lease was lost.
    recompute.assert_not_called()


@pytest.mark.asyncio
async def test_run_step_handler_raises_promotes_to_error_with_retries_left():
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    rt = _build_runtime_mocks()
    rs = _make_run_step(retries=3)
    rt["workflow_def"].item_steps[WorkflowStepType.PARSE].method = AsyncMock(side_effect=RuntimeError("boom"))
    events: list[LifeCycleEvent] = []

    async def collect(env):
        events.append(env.event)

    w.lifecycle.subscribe(collect)
    with (
        _patch_runtime_lookups(rt),
        _patch_op("error_step", return_value=RunStatus.ERROR),
        _patch_op("recompute_run_status"),
    ):
        await w._run_step(rs, "lease-1", 0)
        await w.lifecycle.drain(timeout=2.0)
    # ERROR (retries-left) emits STEP_END.
    assert LifeCycleEvent.STEP_END in events
    assert LifeCycleEvent.STEP_FAILED not in events


@pytest.mark.asyncio
async def test_run_step_handler_raises_then_retries_exhausted_emits_step_failed():
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    rt = _build_runtime_mocks()
    rs = _make_run_step(retries=1)
    rt["workflow_def"].item_steps[WorkflowStepType.PARSE].method = AsyncMock(side_effect=RuntimeError("boom"))
    events: list[LifeCycleEvent] = []

    async def collect(env):
        events.append(env.event)

    w.lifecycle.subscribe(collect)
    with (
        _patch_runtime_lookups(rt),
        _patch_op("error_step", return_value=RunStatus.FAILED),
        _patch_op("recompute_run_status"),
    ):
        await w._run_step(rs, "lease-1", 0)
        await w.lifecycle.drain(timeout=2.0)
    assert LifeCycleEvent.STEP_FAILED in events


@pytest.mark.asyncio
async def test_run_step_handler_raises_then_lease_lost_returns():
    w = Worker(config=WorkerConfig(consumers={"*": 1}), metrics=MagicMock())
    rt = _build_runtime_mocks()
    rs = _make_run_step()
    rt["workflow_def"].item_steps[WorkflowStepType.PARSE].method = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        _patch_runtime_lookups(rt),
        _patch_op("error_step", return_value=None),
        _patch_op("recompute_run_status") as recompute,
    ):
        await w._run_step(rs, "lease-1", 0)
    w._metrics.incr.assert_any_call("lease_lost", phase="error")
    recompute.assert_not_called()


@pytest.mark.asyncio
async def test_run_step_error_step_itself_raises_is_logged_not_propagated(caplog):
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    rt = _build_runtime_mocks()
    rs = _make_run_step()
    rt["workflow_def"].item_steps[WorkflowStepType.PARSE].method = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        _patch_runtime_lookups(rt),
        _patch_op("error_step", side_effect=RuntimeError("db down")),
    ):
        with caplog.at_level(logging.ERROR, logger="soliplex.ingester.lib.wf.runner"):
            await w._run_step(rs, "lease-1", 0)
    assert any("error_step bookkeeping failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_step_cancelled_releases_step_and_reraises():
    w = Worker(config=WorkerConfig(consumers={"*": 1}), metrics=MagicMock())
    rt = _build_runtime_mocks()
    rs = _make_run_step()
    rt["workflow_def"].item_steps[WorkflowStepType.PARSE].method = AsyncMock(side_effect=asyncio.CancelledError)
    with (
        _patch_runtime_lookups(rt),
        _patch_op("release_step", return_value=True) as release,
    ):
        with pytest.raises(asyncio.CancelledError):
            await w._run_step(rs, "lease-1", 0)
        release.assert_awaited_once_with(1, "lease-1")
    w._metrics.incr.assert_any_call("step_released")


@pytest.mark.asyncio
async def test_run_step_cancelled_release_returns_false_no_metric():
    """If release_step finds the lease no longer matches, the metric
    is not bumped — only successful releases count."""
    w = Worker(config=WorkerConfig(consumers={"*": 1}), metrics=MagicMock())
    rt = _build_runtime_mocks()
    rs = _make_run_step()
    rt["workflow_def"].item_steps[WorkflowStepType.PARSE].method = AsyncMock(side_effect=asyncio.CancelledError)
    with (
        _patch_runtime_lookups(rt),
        _patch_op("release_step", return_value=False),
    ):
        with pytest.raises(asyncio.CancelledError):
            await w._run_step(rs, "lease-1", 0)
    # released=False → no step_released metric.
    for call in w._metrics.incr.call_args_list:
        assert call.args[0] != "step_released"


@pytest.mark.asyncio
async def test_run_step_handler_raises_before_workflow_run_loaded_skips_lifecycle_publish():
    """If the very first ``get_workflow_run`` call raises, the error
    handler still runs but skips the lifecycle publish (workflow_def
    / workflow_run / run_group are all None)."""
    w = Worker(config=WorkerConfig(consumers={"*": 1}), metrics=MagicMock())
    rs = _make_run_step()
    seen: list[LifeCycleEvent] = []

    async def collect(env):
        seen.append(env.event)

    w.lifecycle.subscribe(collect)
    with (
        _patch_op("get_workflow_run", side_effect=RuntimeError("boom")),
        _patch_op("error_step", return_value=RunStatus.ERROR),
        _patch_op("recompute_run_status"),
    ):
        await w._run_step(rs, "lease-1", 0)
        await w.lifecycle.drain(timeout=1.0)
    # We never published anything since workflow_def is None.
    assert seen == []


@pytest.mark.asyncio
async def test_stop_waits_for_inflight_steps_until_deadline():
    """If a step is in flight, ``stop`` polls until either the in-
    flight set drains or the deadline elapses."""
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    with _patch_loop_ops():
        await w.start()
        # Pretend a consumer claimed a step that is still running.
        w._inflight[42] = _InFlight(step_id=42, lease="x", resource_key=None)

        async def drain_inflight():
            await asyncio.sleep(0.2)
            w._inflight.pop(42, None)

        drainer = asyncio.create_task(drain_inflight())
        await w.stop(timeout=2.0)
        await drainer
        assert 42 not in w._inflight


@pytest.mark.asyncio
async def test_run_step_cancelled_release_failure_is_logged_but_reraises(caplog):
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    rt = _build_runtime_mocks()
    rs = _make_run_step()
    rt["workflow_def"].item_steps[WorkflowStepType.PARSE].method = AsyncMock(side_effect=asyncio.CancelledError)
    with (
        _patch_runtime_lookups(rt),
        _patch_op("release_step", side_effect=RuntimeError("db lost")),
    ):
        with caplog.at_level(logging.ERROR, logger="soliplex.ingester.lib.wf.runner"):
            with pytest.raises(asyncio.CancelledError):
                await w._run_step(rs, "lease-1", 0)
    assert any("release_step failed during shutdown" in r.message for r in caplog.records)


# ---------------------------------------------------------------------
# Worker._consumer_loop
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_loop_idle_increments_backoff_and_sleeps():
    w = Worker(
        config=WorkerConfig(
            consumers={"*": 1},
            poll_interval=0.0,
            poll_backoff_max=0.0,
        ),
    )
    # Force two empty claim rounds, then signal stop.
    call_count = {"n": 0}

    async def fake_claim(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            w._stop_event.set()
        return None

    with patch("soliplex.ingester.lib.wf.runner.operations.claim_next_step", new=fake_claim):
        await asyncio.wait_for(w._consumer_loop("*", 0), timeout=2.0)
    assert call_count["n"] >= 2


@pytest.mark.asyncio
async def test_consumer_loop_claim_error_is_logged_and_continues(caplog):
    w = Worker(
        config=WorkerConfig(
            consumers={"*": 1},
            poll_interval=0.0,
            poll_backoff_max=0.0,
        ),
        metrics=MagicMock(),
    )
    call_count = {"n": 0}

    async def fake_claim(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("transient db error")
        w._stop_event.set()
        return None

    with caplog.at_level(logging.ERROR, logger="soliplex.ingester.lib.wf.runner"):
        with patch("soliplex.ingester.lib.wf.runner.operations.claim_next_step", new=fake_claim):
            await asyncio.wait_for(w._consumer_loop("*", 0), timeout=2.0)
    assert any("claim error in consumer" in r.message for r in caplog.records)
    w._metrics.incr.assert_any_call("claim_error", pool="*")


@pytest.mark.asyncio
async def test_consumer_loop_runs_step_when_claim_returns_one():
    w = Worker(
        config=WorkerConfig(
            consumers={"*": 1},
            poll_interval=0.0,
            poll_backoff_max=0.0,
        ),
    )
    rs = _make_run_step()

    async def fake_claim(*args, **kwargs):
        if not getattr(fake_claim, "served", False):
            fake_claim.served = True
            return rs
        w._stop_event.set()
        return None

    with (
        patch("soliplex.ingester.lib.wf.runner.operations.claim_next_step", new=fake_claim),
        patch.object(w, "_run_step", new_callable=AsyncMock) as run_step,
    ):
        await asyncio.wait_for(w._consumer_loop("*", 0), timeout=2.0)
    run_step.assert_awaited()


@pytest.mark.asyncio
async def test_consumer_loop_backs_off_after_race_release():
    """When ``_run_step`` returns True (race-release), the consumer
    should treat it like an idle claim: emit ``claim_lost_race``
    and apply backoff so it doesn't spin-claim until the lock
    holder finishes."""
    metrics = MagicMock()
    w = Worker(
        config=WorkerConfig(
            consumers={"*": 1},
            poll_interval=0.0,
            poll_backoff_max=0.0,
        ),
        metrics=metrics,
    )
    rs = _make_run_step(resource_key="rag:/tmp/db")

    call_count = 0

    async def fake_claim(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return rs
        w._stop_event.set()
        return None

    async def fake_run_step(*args, **kwargs):
        return True  # signal race-release

    with (
        patch("soliplex.ingester.lib.wf.runner.operations.claim_next_step", new=fake_claim),
        patch.object(w, "_run_step", new=fake_run_step),
    ):
        await asyncio.wait_for(w._consumer_loop("*", 0), timeout=2.0)

    metrics.incr.assert_any_call("claim_lost_race", pool="*")


@pytest.mark.asyncio
async def test_consumer_loop_propagates_cancellation():
    w = Worker(config=WorkerConfig(consumers={"*": 1}, poll_interval=10))
    blocked = asyncio.Event()

    async def fake_claim(*args, **kwargs):
        blocked.set()
        await asyncio.sleep(10)

    with patch("soliplex.ingester.lib.wf.runner.operations.claim_next_step", new=fake_claim):
        task = asyncio.create_task(w._consumer_loop("*", 0))
        await blocked.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------
# Background loops (heartbeat / reaper / sweeper)
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_loop_refreshes_inflight_resource_locks():
    w = Worker(config=WorkerConfig(consumers={"*": 1}, checkin_interval=0))
    w._inflight[1] = _InFlight(step_id=1, lease="lease-1", resource_key="rag:/tmp/db")

    async def fake_heartbeat(_):
        w._stop_event.set()

    with (
        patch("soliplex.ingester.lib.wf.runner.operations.worker_heartbeat", new=fake_heartbeat),
        patch(
            "soliplex.ingester.lib.wf.runner.operations.refresh_resource_lock",
            new_callable=AsyncMock,
            return_value=True,
        ) as refresh,
    ):
        await asyncio.wait_for(w._heartbeat_loop(), timeout=2.0)
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_heartbeat_loop_skips_refresh_when_no_resource_key():
    """The refresh-resource-lock branch is gated on
    ``inflight.resource_key`` being truthy."""
    w = Worker(config=WorkerConfig(consumers={"*": 1}, checkin_interval=0))
    w._inflight[1] = _InFlight(step_id=1, lease="lease-1", resource_key=None)

    async def fake_heartbeat(_):
        w._stop_event.set()

    with (
        patch("soliplex.ingester.lib.wf.runner.operations.worker_heartbeat", new=fake_heartbeat),
        patch(
            "soliplex.ingester.lib.wf.runner.operations.refresh_resource_lock",
            new_callable=AsyncMock,
        ) as refresh,
    ):
        await asyncio.wait_for(w._heartbeat_loop(), timeout=2.0)
    refresh.assert_not_called()


@pytest.mark.asyncio
async def test_heartbeat_loop_propagates_cancellation():
    w = Worker(config=WorkerConfig(consumers={"*": 1}, checkin_interval=10))
    started = asyncio.Event()

    async def fake_heartbeat(_):
        started.set()
        await asyncio.sleep(10)

    with patch("soliplex.ingester.lib.wf.runner.operations.worker_heartbeat", new=fake_heartbeat):
        task = asyncio.create_task(w._heartbeat_loop())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_reaper_loop_propagates_cancellation():
    w = Worker(config=WorkerConfig(consumers={"*": 1}, checkin_timeout=0))
    started = asyncio.Event()

    async def fake_reap(*args, **kwargs):
        started.set()
        await asyncio.sleep(10)

    with patch("soliplex.ingester.lib.wf.runner.operations.reap_dead_workers", new=fake_reap):
        task = asyncio.create_task(w._reaper_loop())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def _wait_for_counter(loop_method_name: str):
    """Build a fake :func:`asyncio.wait_for` that times out the
    first call from the named loop method and then passes through.

    Patching ``asyncio.wait_for`` is global (asyncio is a singleton
    module), so the fake also runs for any ``wait_for`` call the
    test wrapper might make. We disambiguate by inspecting the
    awaited coroutine: the loops' ``wait_for`` calls receive
    ``self._stop_event.wait()`` as the coroutine.
    """
    orig_wait_for = asyncio.wait_for
    state = {"n": 0}

    async def fake_wait_for(coro, timeout=None):
        # Heuristic: the loops' wait_for calls operate on a
        # ``stop_event.wait()`` coroutine; everything else (including
        # the test wrapper) goes through the real implementation.
        cr_name = getattr(coro, "__qualname__", "") or getattr(coro, "cr_code", None)
        cr_code_name = getattr(getattr(coro, "cr_code", None), "co_name", "")
        is_event_wait = cr_code_name == "wait" or "Event.wait" in str(cr_name)
        if not is_event_wait:
            return await orig_wait_for(coro, timeout=timeout)
        state["n"] += 1
        if state["n"] == 1:
            coro.close()
            raise TimeoutError()
        return await orig_wait_for(coro, timeout=timeout)

    return fake_wait_for, state


@pytest.mark.asyncio
async def test_heartbeat_loop_continues_after_interval_elapses():
    w = Worker(config=WorkerConfig(consumers={"*": 1}, checkin_interval=10))
    body_calls = {"n": 0}

    async def fake_heartbeat(_):
        body_calls["n"] += 1
        if body_calls["n"] >= 2:
            w._stop_event.set()

    fake_wait, _ = _wait_for_counter("_heartbeat_loop")
    with (
        patch("soliplex.ingester.lib.wf.runner.operations.worker_heartbeat", new=fake_heartbeat),
        patch("soliplex.ingester.lib.wf.runner.asyncio.wait_for", new=fake_wait),
    ):
        async with asyncio.timeout(2.0):
            await w._heartbeat_loop()
    assert body_calls["n"] >= 2


@pytest.mark.asyncio
async def test_reaper_loop_continues_after_interval_elapses():
    w = Worker(config=WorkerConfig(consumers={"*": 1}, checkin_timeout=10))
    body_calls = {"n": 0}

    async def fake_reap(*args, **kwargs):
        body_calls["n"] += 1
        if body_calls["n"] >= 2:
            w._stop_event.set()
        return [], []

    fake_wait, _ = _wait_for_counter("_reaper_loop")
    with (
        patch("soliplex.ingester.lib.wf.runner.operations.reap_dead_workers", new=fake_reap),
        patch("soliplex.ingester.lib.wf.runner.asyncio.wait_for", new=fake_wait),
    ):
        async with asyncio.timeout(2.0):
            await w._reaper_loop()
    assert body_calls["n"] >= 2


@pytest.mark.asyncio
async def test_lock_sweeper_loop_continues_after_interval_elapses():
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    body_calls = {"n": 0}

    async def fake_sweep():
        body_calls["n"] += 1
        if body_calls["n"] >= 2:
            w._stop_event.set()
        # Returning 0 also exercises the "no-op when nothing swept"
        # branch (the `if count:` is False on the first iteration).
        return 0

    fake_wait, _ = _wait_for_counter("_lock_sweeper_loop")
    with (
        patch("soliplex.ingester.lib.wf.runner.operations.sweep_expired_resource_locks", new=fake_sweep),
        patch("soliplex.ingester.lib.wf.runner.asyncio.wait_for", new=fake_wait),
    ):
        async with asyncio.timeout(2.0):
            await w._lock_sweeper_loop()
    assert body_calls["n"] >= 2


@pytest.mark.asyncio
async def test_lock_sweeper_loop_propagates_cancellation():
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    started = asyncio.Event()

    async def fake_sweep():
        started.set()
        await asyncio.sleep(120)

    with patch("soliplex.ingester.lib.wf.runner.operations.sweep_expired_resource_locks", new=fake_sweep):
        task = asyncio.create_task(w._lock_sweeper_loop())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_heartbeat_loop_warns_on_lost_resource_lock(caplog):
    w = Worker(config=WorkerConfig(consumers={"*": 1}, checkin_interval=0))
    w._inflight[1] = _InFlight(step_id=1, lease="lease-1", resource_key="rag:/tmp/db")

    async def fake_heartbeat(_):
        w._stop_event.set()

    with (
        patch("soliplex.ingester.lib.wf.runner.operations.worker_heartbeat", new=fake_heartbeat),
        patch(
            "soliplex.ingester.lib.wf.runner.operations.refresh_resource_lock",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        with caplog.at_level(logging.WARNING, logger="soliplex.ingester.lib.wf.runner"):
            await asyncio.wait_for(w._heartbeat_loop(), timeout=2.0)
    assert any("lost resource lock" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_heartbeat_loop_logs_and_continues_on_db_error(caplog):
    w = Worker(config=WorkerConfig(consumers={"*": 1}, checkin_interval=0))

    async def fake_heartbeat(_):
        # Set stop before raising so the post-iteration wait_for
        # exits via the stop_event and the loop returns. The error
        # is still logged, which is what we're verifying.
        w._stop_event.set()
        raise RuntimeError("transient")

    with patch("soliplex.ingester.lib.wf.runner.operations.worker_heartbeat", new=fake_heartbeat):
        with caplog.at_level(logging.ERROR, logger="soliplex.ingester.lib.wf.runner"):
            await asyncio.wait_for(w._heartbeat_loop(), timeout=2.0)
    assert any("heartbeat loop iteration failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_reaper_loop_logs_when_workers_reaped():
    w = Worker(config=WorkerConfig(consumers={"*": 1}, checkin_timeout=0), metrics=MagicMock())

    async def fake_reap(*args, **kwargs):
        w._stop_event.set()
        return ["dead-worker"], [42]

    with patch("soliplex.ingester.lib.wf.runner.operations.reap_dead_workers", new=fake_reap):
        await asyncio.wait_for(w._reaper_loop(), timeout=2.0)
    w._metrics.incr.assert_any_call("worker_reaped", value=1)
    w._metrics.incr.assert_any_call("step_reset_by_reaper", value=1)


@pytest.mark.asyncio
async def test_reaper_loop_silent_when_nothing_to_reap():
    w = Worker(config=WorkerConfig(consumers={"*": 1}, checkin_timeout=0), metrics=MagicMock())

    async def fake_reap(*args, **kwargs):
        w._stop_event.set()
        return [], []

    with patch("soliplex.ingester.lib.wf.runner.operations.reap_dead_workers", new=fake_reap):
        await asyncio.wait_for(w._reaper_loop(), timeout=2.0)
    # No metric calls for reaped workers.
    for call in w._metrics.incr.call_args_list:
        assert call.args[0] != "worker_reaped"


@pytest.mark.asyncio
async def test_reaper_loop_logs_db_errors(caplog):
    w = Worker(config=WorkerConfig(consumers={"*": 1}, checkin_timeout=0))

    async def fake_reap(*args, **kwargs):
        w._stop_event.set()
        raise RuntimeError("transient")

    with caplog.at_level(logging.ERROR, logger="soliplex.ingester.lib.wf.runner"):
        with patch("soliplex.ingester.lib.wf.runner.operations.reap_dead_workers", new=fake_reap):
            await asyncio.wait_for(w._reaper_loop(), timeout=2.0)
    assert any("reaper loop iteration failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_lock_sweeper_loop_emits_metric_when_locks_swept():
    w = Worker(config=WorkerConfig(consumers={"*": 1}), metrics=MagicMock())

    async def fake_sweep():
        w._stop_event.set()
        return 3

    with patch("soliplex.ingester.lib.wf.runner.operations.sweep_expired_resource_locks", new=fake_sweep):
        await asyncio.wait_for(w._lock_sweeper_loop(), timeout=2.0)
    w._metrics.incr.assert_any_call("resource_lock_swept", value=3)


@pytest.mark.asyncio
async def test_lock_sweeper_loop_logs_db_errors(caplog):
    w = Worker(config=WorkerConfig(consumers={"*": 1}))

    async def fake_sweep():
        w._stop_event.set()
        raise RuntimeError("transient")

    with caplog.at_level(logging.ERROR, logger="soliplex.ingester.lib.wf.runner"):
        with patch("soliplex.ingester.lib.wf.runner.operations.sweep_expired_resource_locks", new=fake_sweep):
            await asyncio.wait_for(w._lock_sweeper_loop(), timeout=2.0)
    assert any("lock sweeper iteration failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------
# Default lifecycle hooks
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_lifecycle_hook_fires_group_end_when_run_group_completes():
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    w._install_default_lifecycle_hooks()
    captured: list[LifeCycleEvent] = []

    async def collect(env):
        captured.append(env.event)

    w.lifecycle.subscribe(collect)
    env = _make_envelope(LifeCycleEvent.STEP_END)
    with patch(
        "soliplex.ingester.lib.wf.runner.operations.try_complete_run_group",
        new_callable=AsyncMock,
        return_value=RunStatus.COMPLETED,
    ):
        await w.lifecycle.publish(env)
        await w.lifecycle.drain(timeout=2.0)
    assert LifeCycleEvent.GROUP_END in captured


@pytest.mark.asyncio
async def test_default_lifecycle_hook_no_op_for_non_terminating_events():
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    w._install_default_lifecycle_hooks()
    env = _make_envelope(LifeCycleEvent.STEP_START)
    with patch(
        "soliplex.ingester.lib.wf.runner.operations.try_complete_run_group",
        new_callable=AsyncMock,
    ) as try_complete:
        await w.lifecycle.publish(env)
        await w.lifecycle.drain(timeout=2.0)
    try_complete.assert_not_called()


@pytest.mark.asyncio
async def test_default_lifecycle_hook_no_op_when_run_group_still_running():
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    w._install_default_lifecycle_hooks()
    captured: list[LifeCycleEvent] = []

    async def collect(env):
        captured.append(env.event)

    w.lifecycle.subscribe(collect)
    env = _make_envelope(LifeCycleEvent.STEP_END)
    with patch(
        "soliplex.ingester.lib.wf.runner.operations.try_complete_run_group",
        new_callable=AsyncMock,
        return_value=None,
    ):
        await w.lifecycle.publish(env)
        await w.lifecycle.drain(timeout=2.0)
    assert LifeCycleEvent.GROUP_END not in captured


def test_install_default_lifecycle_hooks_is_idempotent():
    w = Worker(config=WorkerConfig(consumers={"*": 1}))
    w._install_default_lifecycle_hooks()
    n1 = len(w._lifecycle._subscribers)
    w._install_default_lifecycle_hooks()
    assert len(w._lifecycle._subscribers) == n1


# ---------------------------------------------------------------------
# _run_workflow_lifecycle_handlers
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_handlers_no_op_when_lifecycle_events_is_none():
    env = _make_envelope()
    env.workflow_def.lifecycle_events = None
    with _patch_op("create_lifecycle_history") as create:
        await _run_workflow_lifecycle_handlers(env)
    create.assert_not_called()


@pytest.mark.asyncio
async def test_lifecycle_handlers_no_op_when_event_not_subscribed():
    env = _make_envelope(LifeCycleEvent.STEP_START)
    env.workflow_def.lifecycle_events = {LifeCycleEvent.GROUP_END: [MagicMock()]}
    with _patch_op("create_lifecycle_history") as create:
        await _run_workflow_lifecycle_handlers(env)
    create.assert_not_called()


@pytest.mark.asyncio
async def test_lifecycle_handlers_records_completed_history_on_success():
    env = _make_envelope(LifeCycleEvent.STEP_END)
    handler = MagicMock()
    handler.name = "h"
    handler.method = AsyncMock(return_value={"ok": True})
    handler.parameters = {}
    env.workflow_def.lifecycle_events = {LifeCycleEvent.STEP_END: [handler]}
    with (
        _patch_op("get_step_config_by_id", return_value=MagicMock(step_type=WorkflowStepType.PARSE)),
        _patch_op("create_lifecycle_history", return_value=MagicMock(id=99)),
        _patch_op("update_lifecycle_history") as update,
    ):
        await _run_workflow_lifecycle_handlers(env)
    update.assert_awaited_once()
    args, kwargs = update.await_args
    assert args[0] == 99
    assert args[1] == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_lifecycle_handlers_records_failed_history_when_handler_raises():
    env = _make_envelope(LifeCycleEvent.STEP_END)
    handler = MagicMock()
    handler.name = "h"
    handler.method = AsyncMock(side_effect=RuntimeError("boom"))
    handler.parameters = {}
    env.workflow_def.lifecycle_events = {LifeCycleEvent.STEP_END: [handler]}
    with (
        _patch_op("get_step_config_by_id", return_value=MagicMock(step_type=WorkflowStepType.PARSE)),
        _patch_op("create_lifecycle_history", return_value=MagicMock(id=99)),
        _patch_op("update_lifecycle_history") as update,
    ):
        await _run_workflow_lifecycle_handlers(env)
    # Final call should be FAILED.
    last_args, _ = update.await_args
    assert last_args[1] == RunStatus.FAILED


@pytest.mark.asyncio
async def test_lifecycle_handlers_log_when_history_update_fails(caplog):
    env = _make_envelope(LifeCycleEvent.STEP_END)
    handler = MagicMock()
    handler.name = "h"
    handler.method = AsyncMock(side_effect=RuntimeError("boom"))
    handler.parameters = {}
    env.workflow_def.lifecycle_events = {LifeCycleEvent.STEP_END: [handler]}
    with (
        _patch_op("get_step_config_by_id", return_value=MagicMock(step_type=WorkflowStepType.PARSE)),
        _patch_op("create_lifecycle_history", return_value=MagicMock(id=99)),
        _patch_op("update_lifecycle_history", side_effect=RuntimeError("db down")),
    ):
        with caplog.at_level(logging.ERROR, logger="soliplex.ingester.lib.wf.runner"):
            await _run_workflow_lifecycle_handlers(env)
    assert any("update lifecycle history failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_lifecycle_handlers_wraps_non_dict_return_in_dict():
    """Handler that returns a non-dict (e.g. a string) gets wrapped
    so the lifecycle history's status_meta column is always a dict."""
    env = _make_envelope(LifeCycleEvent.STEP_END)
    handler = MagicMock()
    handler.name = "h"
    handler.method = AsyncMock(return_value="bare string")
    handler.parameters = {}
    env.workflow_def.lifecycle_events = {LifeCycleEvent.STEP_END: [handler]}
    with (
        _patch_op("get_step_config_by_id", return_value=MagicMock(step_type=WorkflowStepType.PARSE)),
        _patch_op("create_lifecycle_history", return_value=MagicMock(id=99)),
        _patch_op("update_lifecycle_history") as update,
    ):
        await _run_workflow_lifecycle_handlers(env)
    kwargs = update.await_args.kwargs
    assert kwargs["status_meta"] == {"result": "bare string"}


# ---------------------------------------------------------------------
# build_coro / build_step_coro
# ---------------------------------------------------------------------


class TestBuildCoro:
    def test_dispatches_via_step_type(self):
        async def fn(run_step, doc_hash):
            return ("ran", doc_hash)

        run_step = MagicMock()
        workflow_run = MagicMock()
        workflow_run.run_params = {}
        workflow_run.doc_id = "h"
        workflow_def = MagicMock()
        handler = MagicMock()
        handler.method = fn
        handler.parameters = {}
        workflow_def.item_steps = {WorkflowStepType.PARSE: handler}
        step_config = MagicMock()
        step_config.step_type = WorkflowStepType.PARSE
        batch = MagicMock(source="src", id=1)
        run_group = MagicMock()
        coro = build_step_coro(run_step, workflow_run, workflow_def, step_config, batch, run_group)
        assert asyncio.run(coro) == ("ran", "h")

    def test_extra_args_override_run_params(self):
        called = {}

        async def fn(custom_param):
            called["v"] = custom_param

        run_step = MagicMock()
        workflow_run = MagicMock()
        workflow_run.run_params = {"custom_param": "from_run"}
        workflow_run.doc_id = "h"
        workflow_def = MagicMock()
        step_config = MagicMock()
        batch = None  # exercises the batch-is-None branch
        run_group = MagicMock()
        handler = MagicMock()
        handler.method = fn
        handler.parameters = {}
        coro = build_coro(
            handler,
            run_step,
            workflow_run,
            workflow_def,
            step_config,
            batch,
            run_group,
            extra_args={"custom_param": "from_extra"},
        )
        asyncio.run(coro)
        assert called["v"] == "from_extra"

    def test_handler_parameters_supply_unbound_args(self):
        called = {}

        async def fn(my_const):
            called["v"] = my_const

        run_step = MagicMock()
        workflow_run = MagicMock()
        workflow_run.run_params = {}
        workflow_run.doc_id = "h"
        workflow_def = MagicMock()
        step_config = MagicMock()
        batch = MagicMock(source="src", id=1)
        run_group = MagicMock()
        handler = MagicMock()
        handler.method = fn
        handler.parameters = {"my_const": 42}
        coro = build_coro(handler, run_step, workflow_run, workflow_def, step_config, batch, run_group)
        asyncio.run(coro)
        assert called["v"] == 42


# ---------------------------------------------------------------------
# Module-level shims
# ---------------------------------------------------------------------


class TestShims:
    @pytest.mark.asyncio
    async def test_get_worker_id_returns_none_when_not_started(self):
        assert get_worker_id() is None

    @pytest.mark.asyncio
    async def test_start_worker_without_tasks_does_not_schedule_loops(self):
        w = await start_worker(create_tasks=False)
        try:
            assert w._tasks == []
            assert get_worker_id() == w.id
        finally:
            await stop_worker()

    @pytest.mark.asyncio
    async def test_start_worker_with_tasks_schedules_loops(self):
        with _patch_loop_ops():
            w = await start_worker(create_tasks=True)
            try:
                assert len(w._tasks) >= 1
            finally:
                await stop_worker()

    @pytest.mark.asyncio
    async def test_start_worker_is_idempotent(self):
        w = await start_worker(create_tasks=False)
        w2 = await start_worker(create_tasks=False)
        try:
            assert w is w2
        finally:
            await stop_worker()

    @pytest.mark.asyncio
    async def test_stop_worker_when_never_started_is_noop(self):
        # Default-worker is None at this point; should not raise.
        await stop_worker()
        assert get_worker_id() is None

    @pytest.mark.asyncio
    async def test_stop_worker_clears_default(self):
        await start_worker(create_tasks=False)
        await stop_worker()
        assert get_worker_id() is None

    @pytest.mark.asyncio
    async def test_get_runnable_steps_empty_db(self, db):
        assert await get_runnable_steps() == []

    @pytest.mark.asyncio
    async def test_get_runnable_steps_top_default_uses_100(self, db):
        # Just make sure default value path is hit.
        assert await get_runnable_steps(top=None) == []


# ---------------------------------------------------------------------
# do_state_transition (legacy wrapper) — extra coverage on top of
# test_runner.py for the ignored *step_worker_id* parameter.
# ---------------------------------------------------------------------


class TestDoStateTransitionStepWorkerIdIgnored:
    def test_step_worker_id_kwarg_is_no_longer_consulted(self):
        # In the old impl, mismatching worker ids raised. After the
        # refactor, ownership is checked at the SQL layer via lease
        # tokens, so step_worker_id is purely positional padding.
        result = do_state_transition(RunStatus.RUNNING, RunStatus.COMPLETED, 0, 1, step_worker_id="someone-else")
        assert result == RunStatus.COMPLETED

    def test_disallowed_transition_still_raises(self):
        with pytest.raises(WorkflowException):
            do_state_transition(RunStatus.PENDING, RunStatus.COMPLETED, 0, 1, step_worker_id=None)


# ---------------------------------------------------------------------
# datetime alias re-export — keeps a few external callers happy that
# import ``runner.datetime`` directly.
# ---------------------------------------------------------------------


def test_datetime_module_is_re_exported():
    assert runner.datetime is datetime
