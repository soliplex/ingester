"""Tests for runner.do_state_transition worker-id and transition guards."""

import pytest

from soliplex.ingester.lib.models import RunStatus
from soliplex.ingester.lib.wf import runner
from soliplex.ingester.lib.wf.runner import WorkflowException
from soliplex.ingester.lib.wf.runner import do_state_transition


@pytest.fixture(autouse=True)
def _set_worker_id(monkeypatch):
    """Set a deterministic worker id for all tests."""
    monkeypatch.setattr(runner, "_worker_id", "worker-A")


class TestAllowedTransitions:
    """Happy-path transitions that must succeed."""

    @pytest.mark.parametrize(
        "start,end",
        [
            (RunStatus.PENDING, RunStatus.RUNNING),
            (RunStatus.RUNNING, RunStatus.COMPLETED),
            (RunStatus.RUNNING, RunStatus.ERROR),
            (RunStatus.ERROR, RunStatus.RUNNING),
        ],
    )
    def test_allowed(self, start, end):
        result = do_state_transition(start, end, 0, 3, "worker-A")
        assert result == end

    def test_same_status_is_noop(self):
        result = do_state_transition(
            RunStatus.RUNNING,
            RunStatus.RUNNING,
            0,
            3,
            "worker-A",
        )
        assert result == RunStatus.RUNNING


class TestDisallowedTransitions:
    """Transitions that must raise WorkflowException."""

    @pytest.mark.parametrize(
        "start,end",
        [
            (RunStatus.PENDING, RunStatus.COMPLETED),
            (RunStatus.PENDING, RunStatus.ERROR),
            (RunStatus.PENDING, RunStatus.FAILED),
            (RunStatus.COMPLETED, RunStatus.RUNNING),
            (RunStatus.FAILED, RunStatus.RUNNING),
        ],
    )
    def test_disallowed(self, start, end):
        with pytest.raises(WorkflowException):
            do_state_transition(start, end, 0, 3, "worker-A")


class TestWorkerIdGuard:
    """Worker ownership checks."""

    def test_rejects_step_owned_by_another_worker(self):
        with pytest.raises(WorkflowException, match="assigned to worker"):
            do_state_transition(
                RunStatus.RUNNING,
                RunStatus.COMPLETED,
                0,
                3,
                "worker-B",
            )

    def test_allows_pending_step_owned_by_another_worker(self):
        """After restart, a new worker can claim orphaned PENDING
        steps still assigned to a dead worker."""
        result = do_state_transition(
            RunStatus.PENDING,
            RunStatus.RUNNING,
            0,
            3,
            "worker-B",
        )
        assert result == RunStatus.RUNNING

    def test_reclaimed_step_rejects_stale_completion(self):
        """Dead-worker race: worker_id cleared, stale worker tries
        to complete."""
        with pytest.raises(WorkflowException, match="reclaimed"):
            do_state_transition(
                RunStatus.PENDING,
                RunStatus.COMPLETED,
                0,
                3,
                None,
            )

    def test_reclaimed_step_rejects_stale_error(self):
        """Dead-worker race: worker_id cleared, stale worker tries
        to set error."""
        with pytest.raises(WorkflowException, match="reclaimed"):
            do_state_transition(
                RunStatus.PENDING,
                RunStatus.ERROR,
                0,
                3,
                None,
            )

    def test_reclaimed_step_allows_fresh_pickup(self):
        """After dead-worker reset, a new worker can pick up the
        PENDING step normally."""
        result = do_state_transition(
            RunStatus.PENDING,
            RunStatus.RUNNING,
            0,
            3,
            None,
        )
        assert result == RunStatus.RUNNING


class TestRetryExhaustion:
    """ERROR with exhausted retries should become FAILED."""

    def test_error_becomes_failed_when_retries_exhausted(self):
        result = do_state_transition(
            RunStatus.RUNNING,
            RunStatus.ERROR,
            3,
            3,
            "worker-A",
        )
        assert result == RunStatus.FAILED

    def test_error_stays_error_when_retries_remain(self):
        result = do_state_transition(
            RunStatus.RUNNING,
            RunStatus.ERROR,
            1,
            3,
            "worker-A",
        )
        assert result == RunStatus.ERROR
