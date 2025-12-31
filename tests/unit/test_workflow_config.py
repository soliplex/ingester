import logging

import pytest
import yaml

import soliplex.ingester.lib.wf.operations as wf_ops
import soliplex.ingester.lib.wf.registry as wf_registry
from soliplex.ingester.lib.models import Database
from soliplex.ingester.lib.models import EventHandler
from soliplex.ingester.lib.models import WorkflowDefinition
from soliplex.ingester.lib.models import WorkflowStepType

logger = logging.getLogger(__name__)


def xtest_create_config():
    parse = EventHandler(
        name="parse",
        step_type="PARSE",
        method="soliplex.ingester.lib.workflow.parse_document",
        parameters={},
    )
    chunk = EventHandler(
        name="parse",
        step_type="CHUNK",
        method="soliplex.ingester.lib.workflow.chunk_document",
        parameters={},
    )
    embed = EventHandler(
        name="parse",
        step_type="EMBED",
        method="soliplex.ingester.lib.workflow.embed_document",
        parameters={},
    )
    store = EventHandler(
        name="parse",
        step_type="STORE",
        method="soliplex.ingester.lib.workflow.save_to_rag",
        parameters={},
    )

    batch_start = EventHandler(
        name="batch_start",
        method="soliplex.ingester.lib.config.get_settings",
        parameters={},
    )
    lifecycle = EventHandler(batch_start=[batch_start])
    cfg = WorkflowDefinition(
        id="test",
        name="test",
        meta={},
        item_steps=[parse, chunk, embed, store],
        life_cycle_events=lifecycle,
    )

    assert cfg
    dicts = cfg.model_dump_json()
    with open("tests/files/test_config.yaml", "w") as f:
        f.write(yaml.dump(yaml.safe_load(dicts)))


@pytest.mark.asyncio
async def test_get_workflow_def():
    wf_loaded = await wf_registry.get_workflow_definition("batch")
    assert wf_loaded


@pytest.mark.asyncio
async def test_load_params():
    wf_loaded = await wf_registry.get_param_set("default")
    assert wf_loaded


def test_make_config():
    steps = {
        "parse": EventHandler(
            name="parse",
            method="soliplex.ingester.lib.workflow.parse_document",
            parameters={},
        ),
        "chunk": EventHandler(
            name="chunk",
            method="soliplex.ingester.lib.workflow.chunk_document",
            parameters={},
        ),
        "embed": EventHandler(
            name="embed",
            method="soliplex.ingester.lib.workflow.embed_document",
            parameters={},
        ),
        "store": EventHandler(
            name="store",
            method="soliplex.ingester.lib.workflow.save_to_rag",
            parameters={},
        ),
    }
    wc = WorkflowDefinition(id="test", name="test", meta={}, item_steps=steps, lifecycle_events={})
    js = wc.model_dump_json()
    yaml.dump(yaml.safe_load(js))


@pytest.mark.asyncio
async def test_get_param_set_base(db: Database):
    base_config = await wf_registry.get_param_set("test_base")
    ids = await wf_ops.get_step_config_ids("test_base")
    assert ids
    assert base_config


@pytest.mark.asyncio
async def test_param_set_comparison_same(db: Database):
    """
    Test comparing two param sets, the ids for the steps before the
    difference should be the same, afterwards should be diffent
    """
    base_ids = await wf_ops.get_step_config_ids("test_base")
    assert base_ids
    same_ids = await wf_ops.get_step_config_ids("test_base_same")
    assert same_ids

    assert base_ids[WorkflowStepType.INGEST] == same_ids[WorkflowStepType.INGEST]
    assert base_ids[WorkflowStepType.VALIDATE] == same_ids[WorkflowStepType.VALIDATE]
    assert base_ids[WorkflowStepType.PARSE] == same_ids[WorkflowStepType.PARSE]
    assert base_ids[WorkflowStepType.CHUNK] == same_ids[WorkflowStepType.CHUNK]
    assert base_ids[WorkflowStepType.EMBED] == same_ids[WorkflowStepType.EMBED]
    assert base_ids[WorkflowStepType.STORE] == same_ids[WorkflowStepType.STORE]
    assert base_ids[WorkflowStepType.ROUTE] == same_ids[WorkflowStepType.ROUTE]


@pytest.mark.asyncio
async def test_param_set_comparison_diff_step(db: Database):
    """
    Test comparing two param sets, the ids for the steps before the
    difference should be the same, afterwards should be diffent
    """
    base_ids = await wf_ops.get_step_config_ids("test_base")
    assert base_ids

    diff_ids = await wf_ops.get_step_config_ids("test_diff_chunk")
    assert diff_ids

    assert base_ids[WorkflowStepType.INGEST] == diff_ids[WorkflowStepType.INGEST]
    assert base_ids[WorkflowStepType.VALIDATE] == diff_ids[WorkflowStepType.VALIDATE]
    assert base_ids[WorkflowStepType.PARSE] == diff_ids[WorkflowStepType.PARSE]
    assert base_ids[WorkflowStepType.CHUNK] != diff_ids[WorkflowStepType.CHUNK]
    assert base_ids[WorkflowStepType.EMBED] != diff_ids[WorkflowStepType.EMBED]
    assert base_ids[WorkflowStepType.STORE] != diff_ids[WorkflowStepType.STORE]
    assert base_ids[WorkflowStepType.ROUTE] != diff_ids[WorkflowStepType.ROUTE]


@pytest.mark.asyncio
async def test_param_set_comparison_missing(db: Database):
    """
    Test comparing two param sets, the ids for the steps before the
    difference should be the same, afterwards should be diffent
    """
    base_ids = await wf_ops.get_step_config_ids("test_base")
    assert base_ids
    diff_ids = await wf_ops.get_step_config_ids("test_missing_chunk")
    assert diff_ids
    assert base_ids[WorkflowStepType.INGEST] == diff_ids[WorkflowStepType.INGEST]
    assert base_ids[WorkflowStepType.VALIDATE] == diff_ids[WorkflowStepType.VALIDATE]
    assert base_ids[WorkflowStepType.PARSE] == diff_ids[WorkflowStepType.PARSE]
    assert base_ids[WorkflowStepType.CHUNK] != diff_ids[WorkflowStepType.CHUNK]
    assert base_ids[WorkflowStepType.EMBED] != diff_ids[WorkflowStepType.EMBED]
    assert base_ids[WorkflowStepType.STORE] != diff_ids[WorkflowStepType.STORE]
    assert base_ids[WorkflowStepType.ROUTE] != diff_ids[WorkflowStepType.ROUTE]


@pytest.mark.asyncio
async def test_param_set_comparison_missing_diff(db: Database):
    """
    Test comparing two param sets, the ids for the steps before the
    difference should be the same, afterwards should be diffent
    """
    base_ids = await wf_ops.get_step_config_ids("test_base")
    assert base_ids
    diff_ids = await wf_ops.get_step_config_ids("test_missing_chunk_diff_param")
    assert diff_ids
    assert base_ids[WorkflowStepType.INGEST] == diff_ids[WorkflowStepType.INGEST]
    assert base_ids[WorkflowStepType.VALIDATE] == diff_ids[WorkflowStepType.VALIDATE]
    assert base_ids[WorkflowStepType.PARSE] != diff_ids[WorkflowStepType.PARSE]
    assert base_ids[WorkflowStepType.CHUNK] != diff_ids[WorkflowStepType.CHUNK]
    assert base_ids[WorkflowStepType.EMBED] != diff_ids[WorkflowStepType.EMBED]
    assert base_ids[WorkflowStepType.STORE] != diff_ids[WorkflowStepType.STORE]
    assert base_ids[WorkflowStepType.ROUTE] != diff_ids[WorkflowStepType.ROUTE]
