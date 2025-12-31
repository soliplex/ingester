import logging

import pytest

import soliplex.ingester.lib.operations as doc_ops

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_create_run_group(db):
    import data as data

    batch_id = await doc_ops.new_batch("pytest", "pytest")
    assert batch_id

    # rg = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="default")
