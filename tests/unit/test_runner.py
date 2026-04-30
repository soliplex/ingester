"""Tests for the runner module's pure state-machine helpers.

The legacy ``do_state_transition`` mixed transition rules with a
worker-ownership check that read process-global state. After the
refactor, ownership is enforced at the SQL layer via lease tokens,
so the in-memory layer is now two pure functions:

* :func:`transition_allowed` — the rules table.
* :func:`elevate_terminal` — ERROR → FAILED when retries exhausted.

:func:`do_state_transition` is preserved as a thin compatibility
wrapper that composes the two; tests for it are kept to the
behaviors it can still meaningfully express.
"""

import pytest

from soliplex.ingester.lib.models import RunStatus
from soliplex.ingester.lib.wf.runner import WorkflowException
from soliplex.ingester.lib.wf.runner import do_state_transition
from soliplex.ingester.lib.wf.runner import elevate_terminal
from soliplex.ingester.lib.wf.runner import transition_allowed


class TestTransitionAllowed:
    """Pure rules table."""

    @pytest.mark.parametrize(
        "start,end",
        [
            (RunStatus.PENDING, RunStatus.RUNNING),
            (RunStatus.RUNNING, RunStatus.COMPLETED),
            (RunStatus.RUNNING, RunStatus.ERROR),
            (RunStatus.RUNNING, RunStatus.PENDING),
            (RunStatus.ERROR, RunStatus.RUNNING),
            (RunStatus.PENDING, RunStatus.CANCELLED),
        ],
    )
    def test_allowed(self, start, end):
        assert transition_allowed(start, end) is True

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
        assert transition_allowed(start, end) is False

    def test_identity_is_allowed(self):
        for s in RunStatus:
            assert transition_allowed(s, s) is True


class TestElevateTerminal:
    """ERROR → FAILED when retries exhausted; otherwise unchanged."""

    def test_error_becomes_failed_when_retries_exhausted(self):
        assert elevate_terminal(RunStatus.ERROR, retry=3, retries=3) == RunStatus.FAILED
        assert elevate_terminal(RunStatus.ERROR, retry=4, retries=3) == RunStatus.FAILED

    def test_error_stays_error_when_retries_remain(self):
        assert elevate_terminal(RunStatus.ERROR, retry=1, retries=3) == RunStatus.ERROR

    def test_other_statuses_unchanged(self):
        for s in (RunStatus.RUNNING, RunStatus.COMPLETED, RunStatus.PENDING):
            assert elevate_terminal(s, retry=99, retries=1) == s


class TestDoStateTransitionWrapper:
    """The legacy wrapper composes the two pure helpers."""

    def test_allowed_returns_end(self):
        assert do_state_transition(RunStatus.PENDING, RunStatus.RUNNING, 0, 3) == RunStatus.RUNNING

    def test_disallowed_raises(self):
        with pytest.raises(WorkflowException, match="can't change"):
            do_state_transition(RunStatus.COMPLETED, RunStatus.RUNNING, 0, 3)

    def test_error_elevates_to_failed(self):
        assert do_state_transition(RunStatus.RUNNING, RunStatus.ERROR, 3, 3) == RunStatus.FAILED

    def test_error_stays_error_with_retries_left(self):
        assert do_state_transition(RunStatus.RUNNING, RunStatus.ERROR, 1, 3) == RunStatus.ERROR
