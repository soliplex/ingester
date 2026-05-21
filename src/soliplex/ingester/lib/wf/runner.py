"""Workflow runner — class-based worker orchestrator.

The persistence seam lives in :mod:`operations`. Everything in this
module is in-memory orchestration over those primitives:

* :class:`Worker` — instance, no module globals. Owns a checkin loop,
  a dead-worker reaper, a lifecycle event bus, a per-resource_key
  in-process mutex map, and a set of typed consumer pools.
  Construction is cheap; multiple workers can coexist in one process
  for tests.
* :class:`Metrics` — pluggable observability protocol. The default
  :class:`LoggingMetrics` no-ops below INFO; production wires
  Prometheus.
* Pure helpers (:func:`transition_allowed`, :func:`elevate_terminal`)
  used to be tangled inside ``do_state_transition``; the lease-token
  gate at the SQL layer (in operations.py) replaces the runtime
  worker-id check that used to live alongside them.

A few module-level shims (:func:`start_worker`, :func:`stop_worker`,
:func:`get_runnable_steps`) are preserved as thin wrappers because
production callers (FastAPI lifespan, CLI ``si-cli worker``) are
small enough to update in this PR but the existing tests reach into
them. Everything new should use :class:`Worker` directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import inspect
import logging
import time
import uuid
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from typing import Protocol

from soliplex.ingester.lib.config import get_settings
from soliplex.ingester.lib.models import DocumentBatch
from soliplex.ingester.lib.models import EventHandler
from soliplex.ingester.lib.models import LifeCycleEvent
from soliplex.ingester.lib.models import RunGroup
from soliplex.ingester.lib.models import RunStatus
from soliplex.ingester.lib.models import RunStep
from soliplex.ingester.lib.models import StepConfig
from soliplex.ingester.lib.models import WorkflowDefinition
from soliplex.ingester.lib.models import WorkflowRun
from soliplex.ingester.lib.models import WorkflowStepType

from . import operations

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# State-machine helpers (pure)
# ---------------------------------------------------------------------


class WorkflowException(Exception):
    pass


_ALLOWED_TRANSITIONS: frozenset[tuple[RunStatus, RunStatus]] = frozenset(
    {
        (RunStatus.PENDING, RunStatus.RUNNING),
        (RunStatus.RUNNING, RunStatus.COMPLETED),
        (RunStatus.RUNNING, RunStatus.ERROR),
        (RunStatus.RUNNING, RunStatus.PENDING),  # graceful release
        (RunStatus.ERROR, RunStatus.RUNNING),
        (RunStatus.PENDING, RunStatus.CANCELLED),  # cascaded by FAILED
    },
)


def transition_allowed(start: RunStatus, end: RunStatus) -> bool:
    """Pure rules table. Returns True iff the transition is legal.

    Identity transitions are allowed (no-op).
    """
    if start == end:
        return True
    return (start, end) in _ALLOWED_TRANSITIONS


def elevate_terminal(end_status: RunStatus, retry: int, retries: int) -> RunStatus:
    """If the caller asked for ERROR but no retries remain, elevate
    to FAILED. Otherwise return *end_status* unchanged."""
    if end_status == RunStatus.ERROR and retry >= retries:
        return RunStatus.FAILED
    return end_status


# ---------------------------------------------------------------------
# Metrics protocol (Phase 8)
# ---------------------------------------------------------------------


class Metrics(Protocol):
    def incr(self, name: str, value: int = 1, **labels: str) -> None: ...
    def observe(self, name: str, value: float, **labels: str) -> None: ...


class LoggingMetrics:
    """Default Metrics impl. Emits at DEBUG so it's effectively a
    no-op in production unless the logger is configured otherwise."""

    def incr(self, name: str, value: int = 1, **labels: str) -> None:
        logger.debug("metric incr %s=%d %s", name, value, labels)

    def observe(self, name: str, value: float, **labels: str) -> None:
        logger.debug("metric observe %s=%.6f %s", name, value, labels)


# ---------------------------------------------------------------------
# Lifecycle event bus
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleEventEnvelope:
    """In-memory event payload published to subscribers."""

    event: LifeCycleEvent
    workflow_def: WorkflowDefinition
    run_step: RunStep
    workflow_run: WorkflowRun
    run_group: RunGroup


LifecycleSubscriber = Callable[[LifecycleEventEnvelope], Awaitable[None]]


class LifecycleBus:
    """Tiny pub/sub. Hooks run on a separate task pool so a slow
    handler can never block step execution.

    GROUP_END is *not* fired here directly — it is emitted by a
    coordinator task that subscribes to step-end events and
    consults :func:`operations.try_complete_run_group` for
    exactly-once semantics across workers.
    """

    def __init__(self) -> None:
        self._subscribers: list[LifecycleSubscriber] = []
        self._pending: set[asyncio.Task[None]] = set()

    def subscribe(self, fn: LifecycleSubscriber) -> None:
        self._subscribers.append(fn)

    async def publish(self, envelope: LifecycleEventEnvelope) -> None:
        for fn in self._subscribers:
            task = asyncio.create_task(self._run_one(fn, envelope))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

    async def drain(self, timeout: float = 5.0) -> None:
        if not self._pending:
            return
        await asyncio.wait(self._pending, timeout=timeout)

    async def _run_one(
        self,
        fn: LifecycleSubscriber,
        envelope: LifecycleEventEnvelope,
    ) -> None:
        try:
            await fn(envelope)
        except Exception:
            logger.exception(
                "lifecycle subscriber failed for event %s",
                envelope.event,
            )


# ---------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------


@dataclass
class WorkerConfig:
    """Knobs that callers may want to override per-instance."""

    consumers: dict[str, int]
    """Map of step type → consumer count. ``"*"`` is the catch-all
    pool that picks up anything not explicitly listed. Pass an empty
    dict to default to ``{"*": worker_task_count}``."""

    poll_interval: float = 1.0
    """Sleep between empty claim attempts."""

    poll_backoff_max: float = 3.0
    """Upper bound on exponential backoff for an idle consumer.
    Tuned for the common case where the contended work (a LanceDB
    write) takes a few seconds — capping here keeps wake-up latency
    low once the lock clears."""

    checkin_interval: int | None = None
    """Override settings.worker_checkin_interval."""

    checkin_timeout: int | None = None
    """Override settings.worker_checkin_timeout."""


class Worker:
    """A workflow worker. Construct, ``await worker.start()``, do
    work, ``await worker.stop()``.

    Single-process consumers serialize on a per-``resource_key``
    in-process ``asyncio.Lock`` — no DB I/O on the hot path and no
    time-based semantics to race against under CPU starvation.

    Cross-process coordination still flows through the database:
    atomic claim with ``SKIP LOCKED``, lease tokens, self-skip in
    the reaper, and the ``ResourceLock`` rendezvous row inserted
    in the same transaction as the claim. Worker-held lock rows
    have a sentinel ``expires_at`` and are cleared by
    ``complete_step`` / ``error_step`` / ``release_step`` /
    ``reap_dead_workers`` rather than by a TTL sweep.
    """

    def __init__(
        self,
        config: WorkerConfig | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        settings = get_settings()
        if config is None:
            config = WorkerConfig(consumers={"*": settings.worker_task_count})
        if not config.consumers:
            config.consumers = {"*": settings.worker_task_count}
        self._config = config
        self._metrics = metrics or LoggingMetrics()
        self._worker_id = str(uuid.uuid4())
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self._inflight: dict[int, _InFlight] = {}
        # Per-resource_key in-process mutex. Two consumer coroutines
        # that end up with steps targeting the same resource (e.g. a
        # LanceDB path) serialize here rather than thrashing on
        # claim-retry. Cross-process serialization is handled by the
        # claim layer.
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._key_locks_guard = asyncio.Lock()
        self._lifecycle = LifecycleBus()
        self._installed_lifecycle_hooks = False

    async def _key_lock(self, key: str) -> asyncio.Lock:
        async with self._key_locks_guard:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._key_locks[key] = lock
            return lock

    # ----- public API -------------------------------------------------

    @property
    def id(self) -> str:
        return self._worker_id

    @property
    def lifecycle(self) -> LifecycleBus:
        return self._lifecycle

    async def start(self) -> None:
        """Bring the worker online. Returns once all background
        tasks have been scheduled."""
        if self._tasks:
            logger.warning("Worker.start called twice; ignoring")
            return
        self._install_default_lifecycle_hooks()
        await operations.worker_heartbeat(self._worker_id)
        self._tasks.append(
            asyncio.create_task(self._heartbeat_loop(), name=f"worker-{self._worker_id}-heartbeat"),
        )
        self._tasks.append(
            asyncio.create_task(self._reaper_loop(), name=f"worker-{self._worker_id}-reaper"),
        )
        for step_type, count in self._config.consumers.items():
            for i in range(count):
                self._tasks.append(
                    asyncio.create_task(
                        self._consumer_loop(step_type, i),
                        name=f"worker-{self._worker_id}-consumer-{step_type}-{i}",
                    ),
                )
        logger.info(
            "started worker %s consumers=%s",
            self._worker_id,
            self._config.consumers,
        )

    async def stop(self, timeout: float = 30.0) -> None:
        """Graceful shutdown.

        1. Signal consumers to stop claiming new work.
        2. Wait up to *timeout* for in-flight steps to finish.
        3. Cancel any consumer still mid-step. Their ``CancelledError``
           handler calls :func:`operations.release_step` so the row
           comes back to PENDING immediately, gated on the lease so
           it can never bounce a fresh claimant.
        4. Drain lifecycle subscribers.
        5. Delete our checkin row so peers don't wait the full
           checkin timeout to discover we left.
        """
        if not self._tasks:
            return
        self._stop_event.set()

        deadline = asyncio.get_event_loop().time() + timeout
        while self._inflight and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.1)

        for task in self._tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        await self._lifecycle.drain(timeout=2.0)

        try:
            await operations.delete_worker_checkin(self._worker_id)
        except Exception:
            logger.exception("worker %s: failed to delete checkin row", self._worker_id)

        logger.info("stopped worker %s", self._worker_id)

    # ----- consumer / claim -------------------------------------------

    def _allowed_types_for(self, step_type: str) -> list[WorkflowStepType] | None:
        """Translate a consumer-pool key into an *allowed_types*
        argument for ``claim_next_step``. The ``"*"`` pool gets
        everything not claimed by an explicit pool."""
        if step_type == "*":
            named = [k for k in self._config.consumers if k != "*"]
            if not named:
                return None
            all_types = [t for t in WorkflowStepType]
            return [t for t in all_types if t.value not in named]
        return [WorkflowStepType(step_type)]

    async def _consumer_loop(self, step_type: str, coro_id: int) -> None:
        allowed = self._allowed_types_for(step_type)
        backoff = self._config.poll_interval
        lc = {"worker_id": self._worker_id, "coro_id": coro_id, "pool": step_type}
        while not self._stop_event.is_set():
            lease = str(uuid.uuid4())
            self._metrics.incr("claim_attempts", pool=step_type)
            t0 = time.monotonic()
            try:
                step = await operations.claim_next_step(
                    self._worker_id,
                    lease,
                    allowed_types=allowed,
                    holder_meta={"worker_id": self._worker_id},
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("claim error in consumer %s", step_type, extra=lc)
                self._metrics.incr("claim_error", pool=step_type)
                await asyncio.sleep(min(backoff, self._config.poll_backoff_max))
                backoff = min(backoff * 2, self._config.poll_backoff_max)
                continue
            self._metrics.observe("claim_duration", time.monotonic() - t0, pool=step_type)
            if step is None:
                self._metrics.incr("claim_idle", pool=step_type)
                await asyncio.sleep(min(backoff, self._config.poll_backoff_max))
                backoff = min(backoff * 2, self._config.poll_backoff_max)
                continue
            self._metrics.incr("claim_success", pool=step_type)
            await self._run_step(step, lease, coro_id)
            backoff = self._config.poll_interval

    async def _run_step(self, run_step: RunStep, lease: str, coro_id: int) -> None:
        """Run one claimed step.

        Cross-process serialization for the step's ``resource_key``
        is already guaranteed by ``claim_next_step`` (atomic
        ResourceLock insert + ``subq_running_rk`` exclusion). In
        addition, this method takes a per-key in-process
        ``asyncio.Lock`` so two consumer coroutines in the same
        worker that somehow ended up with steps targeting the same
        key serialize at the user-code boundary instead of racing
        inside the work.
        """
        lc = {"worker_id": self._worker_id, "coro_id": coro_id, "step_id": run_step.id}
        inflight = _InFlight(step_id=run_step.id, lease=lease, resource_key=run_step.resource_key)
        self._inflight[run_step.id] = inflight
        t0 = time.monotonic()
        workflow_def: WorkflowDefinition | None = None
        workflow_run: WorkflowRun | None = None
        run_group: RunGroup | None = None
        try:
            workflow_run = await operations.get_workflow_run(run_step.workflow_run_id)
            run_group = await operations.get_run_group(workflow_run.run_group_id)
            workflow_def = await operations.get_workflow_definition(
                workflow_run.workflow_definition_id,
            )
            step_config = await operations.get_step_config_by_id(run_step.step_config_id)
            batch = await operations.get_batch(workflow_run.batch_id)

            key_lock: asyncio.Lock | None = None
            if run_step.resource_key:
                key_lock = await self._key_lock(run_step.resource_key)

            logger.info(
                "running step %s (%s) in %s priority=%d attempt=%d/%d",
                run_step.workflow_step_number,
                run_step.workflow_step_name,
                workflow_def.name,
                run_step.priority,
                run_step.retry + 1,
                run_step.retries,
                extra=lc,
            )

            await self._lifecycle.publish(
                LifecycleEventEnvelope(
                    LifeCycleEvent.STEP_START,
                    workflow_def,
                    run_step,
                    workflow_run,
                    run_group,
                ),
            )
            if run_step.workflow_step_number == 1:
                await self._lifecycle.publish(
                    LifecycleEventEnvelope(
                        LifeCycleEvent.ITEM_START,
                        workflow_def,
                        run_step,
                        workflow_run,
                        run_group,
                    ),
                )

            async with key_lock if key_lock is not None else contextlib.nullcontext():
                res = await build_step_coro(
                    run_step,
                    workflow_run,
                    workflow_def,
                    step_config,
                    batch,
                    run_group,
                )
            logger.debug("step %s returned %s", run_step.workflow_step_number, res, extra=lc)

            ok = await operations.complete_step(
                run_step.id,
                lease,
                message="success",
                meta={"coro_id": coro_id},
            )
            if not ok:
                self._metrics.incr("lease_lost", phase="complete")
                logger.warning("lease lost on completion of step %s", run_step.id, extra=lc)
                return
            self._metrics.incr("step_completed")
            await operations.recompute_run_status(run_step.workflow_run_id)
            await self._lifecycle.publish(
                LifecycleEventEnvelope(
                    LifeCycleEvent.STEP_END,
                    workflow_def,
                    run_step,
                    workflow_run,
                    run_group,
                ),
            )
            if run_step.is_last_step:
                await self._lifecycle.publish(
                    LifecycleEventEnvelope(
                        LifeCycleEvent.ITEM_END,
                        workflow_def,
                        run_step,
                        workflow_run,
                        run_group,
                    ),
                )

        except asyncio.CancelledError:
            # Graceful shutdown path. Release the step back to
            # PENDING so it's immediately re-claimable.
            try:
                released = await operations.release_step(run_step.id, lease)
                if released:
                    logger.info("released step %s on shutdown", run_step.id, extra=lc)
                    self._metrics.incr("step_released")
            except Exception:
                logger.exception("release_step failed during shutdown", extra=lc)
            raise
        except Exception as e:
            logger.exception("step %s failed", run_step.id, extra=lc)
            try:
                new_status = await operations.error_step(
                    run_step.id,
                    lease,
                    message=f"exception: {e}",
                    meta={"coro_id": coro_id},
                )
                if new_status is None:
                    self._metrics.incr("lease_lost", phase="error")
                    return
                if new_status == RunStatus.FAILED:
                    self._metrics.incr("step_failed")
                else:
                    self._metrics.incr("step_error")
                await operations.recompute_run_status(run_step.workflow_run_id)
                if workflow_def is not None and workflow_run is not None and run_group is not None:
                    evt = LifeCycleEvent.STEP_FAILED if new_status == RunStatus.FAILED else LifeCycleEvent.STEP_END
                    await self._lifecycle.publish(
                        LifecycleEventEnvelope(
                            evt,
                            workflow_def,
                            run_step,
                            workflow_run,
                            run_group,
                        ),
                    )
            except Exception:
                logger.exception("error_step bookkeeping failed for step %s", run_step.id, extra=lc)
        finally:
            self._inflight.pop(run_step.id, None)
            self._metrics.observe("step_duration", time.monotonic() - t0)

    # ----- background loops -------------------------------------------

    async def _heartbeat_loop(self) -> None:
        settings = get_settings()
        interval = self._config.checkin_interval or settings.worker_checkin_interval
        while not self._stop_event.is_set():
            try:
                await operations.worker_heartbeat(self._worker_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("heartbeat loop iteration failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except TimeoutError:
                continue
            else:
                return

    async def _reaper_loop(self) -> None:
        settings = get_settings()
        timeout = self._config.checkin_timeout or settings.worker_checkin_timeout
        # Run the reaper at half the timeout so a dead worker is
        # noticed within ~1.5 timeouts on average.
        interval = max(30, timeout // 2)
        while not self._stop_event.is_set():
            try:
                reaped, reset = await operations.reap_dead_workers(
                    self._worker_id,
                    threshold_seconds=timeout,
                )
                if reaped:
                    logger.info(
                        "reaped %d dead workers, reset %d steps",
                        len(reaped),
                        len(reset),
                    )
                    self._metrics.incr("worker_reaped", value=len(reaped))
                    self._metrics.incr("step_reset_by_reaper", value=len(reset))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("reaper loop iteration failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except TimeoutError:
                continue
            else:
                return

    # ----- default lifecycle hooks ------------------------------------

    def _install_default_lifecycle_hooks(self) -> None:
        if self._installed_lifecycle_hooks:
            return
        self._installed_lifecycle_hooks = True

        async def dispatch_user_handlers(envelope: LifecycleEventEnvelope) -> None:
            await _run_workflow_lifecycle_handlers(envelope)

        async def maybe_complete_group(envelope: LifecycleEventEnvelope) -> None:
            if envelope.event not in (LifeCycleEvent.STEP_END, LifeCycleEvent.STEP_FAILED, LifeCycleEvent.ITEM_END):
                return
            new_status = await operations.try_complete_run_group(envelope.run_group.id)
            if new_status is None:
                return
            await self._lifecycle.publish(
                LifecycleEventEnvelope(
                    LifeCycleEvent.GROUP_END,
                    envelope.workflow_def,
                    envelope.run_step,
                    envelope.workflow_run,
                    envelope.run_group,
                ),
            )

        self._lifecycle.subscribe(dispatch_user_handlers)
        self._lifecycle.subscribe(maybe_complete_group)


@dataclass
class _InFlight:
    step_id: int
    lease: str
    resource_key: str | None


# ---------------------------------------------------------------------
# Lifecycle dispatcher (executes user-configured EventHandlers)
# ---------------------------------------------------------------------


async def _run_workflow_lifecycle_handlers(envelope: LifecycleEventEnvelope) -> None:
    workflow_def = envelope.workflow_def
    if workflow_def.lifecycle_events is None:
        return
    handlers = workflow_def.lifecycle_events.get(envelope.event)
    if not handlers:
        return
    step_config = await operations.get_step_config_by_id(envelope.run_step.step_config_id)
    for handler in handlers:
        hist_id = None
        try:
            logger.info("lifecycle %s for %s", envelope.event.name, workflow_def.name)
            hist = await operations.create_lifecycle_history(
                envelope.run_group.id,
                envelope.workflow_run.id,
                envelope.event,
                RunStatus.RUNNING,
                envelope.run_step.id,
                handler.name,
            )
            hist_id = hist.id
            res = await build_coro(
                handler,
                envelope.run_step,
                envelope.workflow_run,
                envelope.workflow_def,
                step_config,
                None,
                envelope.run_group,
            )
            if res is None or not isinstance(res, dict):
                res = {"result": str(res)}
            await operations.update_lifecycle_history(
                hist_id,
                RunStatus.COMPLETED,
                status_message="success",
                status_meta=res,
            )
        except Exception as e:
            logger.exception("lifecycle handler %s failed", handler.name)
            try:
                await operations.update_lifecycle_history(
                    hist_id,
                    RunStatus.FAILED,
                    status_meta={"error": str(e)},
                    status_message=str(e),
                )
            except Exception:
                logger.exception("update lifecycle history failed")


# ---------------------------------------------------------------------
# Coroutine builders (unchanged from before)
# ---------------------------------------------------------------------


def build_step_coro(
    run_step: RunStep,
    workflow_run: WorkflowRun,
    workflow_def: WorkflowDefinition,
    step_config: StepConfig,
    batch: DocumentBatch,
    run_group: RunGroup,
):
    """Build a coroutine from a workflow context."""
    workflow_handler = workflow_def.item_steps[step_config.step_type]
    return build_coro(
        workflow_handler,
        run_step,
        workflow_run,
        workflow_def,
        step_config,
        batch,
        run_group,
    )


def build_coro(
    handler: EventHandler,
    run_step: RunStep,
    workflow_run: WorkflowRun,
    workflow_def: WorkflowDefinition,
    step_config: StepConfig,
    batch: DocumentBatch,
    run_group: RunGroup,
    extra_args: dict | None = None,
):
    fn = handler.method
    sig = inspect.signature(fn)
    batch_source = None
    batch_id = None
    if batch is not None:
        batch_source = batch.source
        batch_id = batch.id

    ns = {
        "run_step": run_step,
        "workflow_run": workflow_run,
        "workflow_def": workflow_def,
        "step_config": step_config,
        "batch_id": batch_id,
        "doc_hash": workflow_run.doc_id,
        "source": batch_source,
        "run_group": run_group,
    }
    ns.update(workflow_run.run_params)
    ns.update(handler.parameters)
    if extra_args is not None:
        ns.update(extra_args)
    call = {k: ns[k] for k in sig.parameters.keys() if k in ns}
    return fn(**call)


# ---------------------------------------------------------------------
# Module-level shims
#
# The new architecture is class-based but a few production callers
# (FastAPI startup, CLI ``si-cli worker``) reach in through the old
# entry points. Keep them as thin wrappers around a single private
# Worker so we don't ship unrelated caller updates in this PR.
# ---------------------------------------------------------------------


_default_worker: Worker | None = None


async def start_worker(create_tasks: bool = True) -> Worker:
    """Construct and start the process-wide default worker.

    Returns the worker. ``create_tasks=False`` is a backwards-
    compatible knob for tests that want to exercise the heartbeat
    or claim path manually — in that mode the worker is constructed
    but its background tasks are not scheduled.
    """
    global _default_worker
    if _default_worker is not None:
        return _default_worker
    settings = get_settings()
    worker = Worker(
        config=WorkerConfig(consumers={"*": settings.worker_task_count}),
    )
    if create_tasks:
        await worker.start()
    _default_worker = worker
    return worker


async def stop_worker() -> None:
    global _default_worker
    if _default_worker is None:
        return
    await _default_worker.stop()
    _default_worker = None


def get_worker_id() -> str | None:
    """Return the process-wide default worker's id, if started."""
    if _default_worker is None:
        return None
    return _default_worker.id


# ---- read-only diagnostic helper kept for the existing test suite ----


async def get_runnable_steps(
    top: int | None = None,
    batch_id: int | None = None,
) -> list[RunStep]:
    """Diagnostic-only: list eligible steps without claiming them.

    The runtime now uses :func:`operations.claim_next_step` for the
    actual claim. This function is a read-only wrapper around the
    same eligibility predicates so the existing tests still describe
    "what is runnable right now."
    """
    from sqlalchemy import tuple_
    from sqlmodel import select

    from soliplex.ingester.lib.models import get_session

    if top is None:
        top = 100

    async with get_session() as session:
        from sqlalchemy import func as _func

        completed_statuses = (
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.RUNNING,
            RunStatus.CANCELLED,
        )

        subq_min_step = (
            select(
                RunStep.workflow_run_id,
                _func.min(RunStep.workflow_step_number).label("min_step"),
            )
            .join(WorkflowRun, WorkflowRun.id == RunStep.workflow_run_id)
            .where(RunStep.retry < RunStep.retries)
            .where(RunStep.status.not_in(completed_statuses))
            .where(WorkflowRun.status.not_in([RunStatus.COMPLETED, RunStatus.FAILED]))
        )
        if batch_id is not None:
            subq_min_step = subq_min_step.where(WorkflowRun.batch_id == batch_id)
        subq_min_step = subq_min_step.group_by(RunStep.workflow_run_id).subquery()

        subq_running = select(RunStep.workflow_run_id).where(RunStep.status == RunStatus.RUNNING).distinct().subquery()

        q = (
            select(RunStep)
            .where(
                tuple_(RunStep.workflow_run_id, RunStep.workflow_step_number).in_(
                    select(subq_min_step.c.workflow_run_id, subq_min_step.c.min_step),
                ),
            )
            .where(RunStep.status.not_in(completed_statuses))
            .where(RunStep.workflow_run_id.not_in(select(subq_running.c.workflow_run_id)))
            .order_by(
                RunStep.priority.desc(),
                RunStep.retry,
                RunStep.created_date,
                RunStep.workflow_step_number,
            )
            .limit(top)
        )
        result = await session.exec(q)
        steps = list(result.all())
        if steps:
            session.expunge_all()
        return steps


# ---- legacy do_state_transition export for any straggling callers ----


def do_state_transition(
    start_status: RunStatus,
    end_status: RunStatus,
    retry: int,
    retries: int,
    step_worker_id: str | None = None,
) -> RunStatus:
    """Legacy state-machine entry kept for callers that have not
    moved to the lease-token based approach. Worker-id checks are
    now enforced at the SQL layer via the lease token, so this
    function is now purely the rules table + ERROR→FAILED elevation."""
    if not transition_allowed(start_status, end_status):
        msg = f"can't change from {start_status.value} to {end_status.value}"
        raise WorkflowException(msg)
    return elevate_terminal(end_status, retry, retries)


# Datetime import retained for legacy callers reaching into the
# module to construct timestamps.
__all__ = [
    "Worker",
    "WorkerConfig",
    "Metrics",
    "LoggingMetrics",
    "LifecycleBus",
    "LifecycleEventEnvelope",
    "WorkflowException",
    "transition_allowed",
    "elevate_terminal",
    "build_step_coro",
    "build_coro",
    "start_worker",
    "stop_worker",
    "get_worker_id",
    "get_runnable_steps",
    "do_state_transition",
    "datetime",
]
