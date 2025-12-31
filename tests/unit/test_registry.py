"""Tests for soliplex.ingester.lib.wf.registry module."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from soliplex.ingester.lib.wf import registry


@pytest.fixture
def reset_registries():
    """Reset global registry state before and after each test."""
    registry._workflow_registry = None
    registry._param_registry = None
    yield
    registry._workflow_registry = None
    registry._param_registry = None


@pytest.fixture
def workflow_yaml_content():
    """Sample workflow YAML content."""
    return """
id: test_workflow
name: Test Workflow
meta:
  version: "1.0"
lifecycle_events:
item_steps:
  ingest:
    name: test ingest
    retries: 1
    method: builtins.print
    parameters: {}
"""


@pytest.fixture
def param_yaml_content():
    """Sample param YAML content."""
    return """
id: test_params
config:
  parse:
    do_ocr: false
"""


@pytest.mark.asyncio
async def test_load_workflow_definition(reset_registries, workflow_yaml_content):
    """Test loading a single workflow definition from YAML file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp_file:
        tmp_file.write(workflow_yaml_content)
        tmp_file.flush()
        tmp_path = Path(tmp_file.name)

    try:
        wf = await registry.load_workflow_definition(tmp_path)
        assert wf.id == "test_workflow"
        assert wf.name == "Test Workflow"
        assert wf.meta == {"version": "1.0"}
    finally:
        tmp_path.unlink()


@pytest.mark.asyncio
async def test_load_param_set(reset_registries, param_yaml_content):
    """Test loading a single param set from YAML file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp_file:
        tmp_file.write(param_yaml_content)
        tmp_file.flush()
        tmp_path = Path(tmp_file.name)

    try:
        params = await registry.load_param_set(tmp_path)
        assert params.id == "test_params"
        assert params.config is not None
    finally:
        tmp_path.unlink()


@pytest.mark.asyncio
async def test_load_workflow_registry(reset_registries, workflow_yaml_content):
    """Test loading workflow registry from directory."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create a workflow file
        wf_path = Path(tmp_dir) / "test_workflow.yaml"
        wf_path.write_text(workflow_yaml_content)

        mock_settings = MagicMock()
        mock_settings.workflow_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            reg = await registry.load_workflow_registry()
            assert "test_workflow" in reg
            assert reg["test_workflow"].name == "Test Workflow"


@pytest.mark.asyncio
async def test_load_workflow_registry_caching(reset_registries, workflow_yaml_content):
    """Test that workflow registry is cached."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        wf_path = Path(tmp_dir) / "test_workflow.yaml"
        wf_path.write_text(workflow_yaml_content)

        mock_settings = MagicMock()
        mock_settings.workflow_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            # First load
            reg1 = await registry.load_workflow_registry()
            # Second load should return cached
            reg2 = await registry.load_workflow_registry()
            assert reg1 is reg2


@pytest.mark.asyncio
async def test_load_workflow_registry_force_reload(reset_registries, workflow_yaml_content):
    """Test force reload of workflow registry."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        wf_path = Path(tmp_dir) / "test_workflow.yaml"
        wf_path.write_text(workflow_yaml_content)

        mock_settings = MagicMock()
        mock_settings.workflow_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            # First load
            reg1 = await registry.load_workflow_registry()
            # Force reload should create new registry
            reg2 = await registry.load_workflow_registry(force_reload=True)
            # They should be different objects (reloaded)
            assert reg1 is not reg2


@pytest.mark.asyncio
async def test_load_workflow_registry_duplicate_id(reset_registries):
    """Test that duplicate workflow IDs raise an error."""
    workflow_yaml = """
id: duplicate_id
name: Test Workflow
meta: {}
lifecycle_events:
item_steps:
  ingest:
    name: test
    retries: 1
    method: builtins.print
    parameters: {}
"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create two files with the same workflow ID
        wf_path1 = Path(tmp_dir) / "workflow1.yaml"
        wf_path1.write_text(workflow_yaml)
        wf_path2 = Path(tmp_dir) / "workflow2.yaml"
        wf_path2.write_text(workflow_yaml)

        mock_settings = MagicMock()
        mock_settings.workflow_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            with pytest.raises(ValueError, match="duplicate workflow id"):
                await registry.load_workflow_registry()


@pytest.mark.asyncio
async def test_get_workflow_definition_found(reset_registries, workflow_yaml_content):
    """Test getting a workflow definition that exists."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        wf_path = Path(tmp_dir) / "test_workflow.yaml"
        wf_path.write_text(workflow_yaml_content)

        mock_settings = MagicMock()
        mock_settings.workflow_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            wf = await registry.get_workflow_definition("test_workflow")
            assert wf.id == "test_workflow"


@pytest.mark.asyncio
async def test_get_workflow_definition_not_found(reset_registries, workflow_yaml_content):
    """Test getting a workflow definition that doesn't exist."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        wf_path = Path(tmp_dir) / "test_workflow.yaml"
        wf_path.write_text(workflow_yaml_content)

        mock_settings = MagicMock()
        mock_settings.workflow_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            with pytest.raises(KeyError, match="workflow nonexistent not found"):
                await registry.get_workflow_definition("nonexistent")


@pytest.mark.asyncio
async def test_get_workflow_definition_default(reset_registries, workflow_yaml_content):
    """Test getting workflow definition with default ID from settings."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        wf_path = Path(tmp_dir) / "test_workflow.yaml"
        wf_path.write_text(workflow_yaml_content)

        mock_settings = MagicMock()
        mock_settings.workflow_dir = tmp_dir
        mock_settings.default_workflow_id = "test_workflow"

        with patch.object(registry, "get_settings", return_value=mock_settings):
            wf = await registry.get_workflow_definition(None)
            assert wf.id == "test_workflow"


def test_get_default_workflow_id():
    """Test get_default_wofklow_id function."""
    mock_settings = MagicMock()
    mock_settings.default_workflow_id = "my_default_workflow"

    with patch.object(registry, "get_settings", return_value=mock_settings):
        result = registry.get_default_workflow_id()
        assert result == "my_default_workflow"


def test_get_default_param_id():
    """Test get_default_param_id function."""
    mock_settings = MagicMock()
    mock_settings.default_param_id = "my_default_params"

    with patch.object(registry, "get_settings", return_value=mock_settings):
        result = registry.get_default_param_id()
        assert result == "my_default_params"


@pytest.mark.asyncio
async def test_load_param_registry(reset_registries, param_yaml_content):
    """Test loading param registry from directory."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        param_path = Path(tmp_dir) / "test_params.yaml"
        param_path.write_text(param_yaml_content)

        mock_settings = MagicMock()
        mock_settings.param_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            reg = await registry.load_param_registry()
            assert "test_params" in reg
            assert reg["test_params"].id == "test_params"


@pytest.mark.asyncio
async def test_load_param_registry_caching(reset_registries, param_yaml_content):
    """Test that param registry is cached."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        param_path = Path(tmp_dir) / "test_params.yaml"
        param_path.write_text(param_yaml_content)

        mock_settings = MagicMock()
        mock_settings.param_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            reg1 = await registry.load_param_registry()
            reg2 = await registry.load_param_registry()
            assert reg1 is reg2


@pytest.mark.asyncio
async def test_load_param_registry_force_reload(reset_registries, param_yaml_content):
    """Test force reload of param registry."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        param_path = Path(tmp_dir) / "test_params.yaml"
        param_path.write_text(param_yaml_content)

        mock_settings = MagicMock()
        mock_settings.param_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            reg1 = await registry.load_param_registry()
            reg2 = await registry.load_param_registry(force_reload=True)
            assert reg1 is not reg2


@pytest.mark.asyncio
async def test_load_param_registry_duplicate_id(reset_registries):
    """Test that duplicate param set IDs raise an error."""
    param_yaml = """
id: duplicate_param
config:
  parse:
    do_ocr: false
"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        param_path1 = Path(tmp_dir) / "params1.yaml"
        param_path1.write_text(param_yaml)
        param_path2 = Path(tmp_dir) / "params2.yaml"
        param_path2.write_text(param_yaml)

        mock_settings = MagicMock()
        mock_settings.param_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            with pytest.raises(ValueError, match="duplicate param set id"):
                await registry.load_param_registry()


@pytest.mark.asyncio
async def test_get_param_set_found(reset_registries, param_yaml_content):
    """Test getting a param set that exists."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        param_path = Path(tmp_dir) / "test_params.yaml"
        param_path.write_text(param_yaml_content)

        mock_settings = MagicMock()
        mock_settings.param_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            params = await registry.get_param_set("test_params")
            assert params.id == "test_params"


@pytest.mark.asyncio
async def test_get_param_set_not_found(reset_registries, param_yaml_content):
    """Test getting a param set that doesn't exist."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        param_path = Path(tmp_dir) / "test_params.yaml"
        param_path.write_text(param_yaml_content)

        mock_settings = MagicMock()
        mock_settings.param_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            with pytest.raises(KeyError, match="param set nonexistent not found"):
                await registry.get_param_set("nonexistent")


@pytest.mark.asyncio
async def test_get_param_set_default(reset_registries, param_yaml_content):
    """Test getting param set with default ID from settings."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        param_path = Path(tmp_dir) / "test_params.yaml"
        param_path.write_text(param_yaml_content)

        mock_settings = MagicMock()
        mock_settings.param_dir = tmp_dir
        mock_settings.default_param_id = "test_params"

        with patch.object(registry, "get_settings", return_value=mock_settings):
            params = await registry.get_param_set(None)
            assert params.id == "test_params"


@pytest.mark.asyncio
async def test_get_workflow_definition_reload_finds_new_file(reset_registries):
    """Test that get_workflow_definition finds workflow after reload when file added."""
    workflow_yaml1 = """
id: workflow1
name: Workflow 1
meta: {}
lifecycle_events:
item_steps:
  ingest:
    name: test
    retries: 1
    method: builtins.print
    parameters: {}
"""
    workflow_yaml2 = """
id: workflow2
name: Workflow 2
meta: {}
lifecycle_events:
item_steps:
  ingest:
    name: test
    retries: 1
    method: builtins.print
    parameters: {}
"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Start with one workflow
        wf_path1 = Path(tmp_dir) / "workflow1.yaml"
        wf_path1.write_text(workflow_yaml1)

        mock_settings = MagicMock()
        mock_settings.workflow_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            # Load initial registry
            await registry.load_workflow_registry()

            # Add another workflow file
            wf_path2 = Path(tmp_dir) / "workflow2.yaml"
            wf_path2.write_text(workflow_yaml2)

            # get_workflow_definition should reload and find the new workflow
            wf = await registry.get_workflow_definition("workflow2")
            assert wf.id == "workflow2"
            assert wf.name == "Workflow 2"


@pytest.mark.asyncio
async def test_get_param_set_reload_finds_new_file(reset_registries):
    """Test that get_param_set finds param set after reload when file added."""
    param_yaml1 = """
id: params1
config:
  parse:
    do_ocr: false
"""
    param_yaml2 = """
id: params2
config:
  parse:
    do_ocr: true
"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Start with one param file
        param_path1 = Path(tmp_dir) / "params1.yaml"
        param_path1.write_text(param_yaml1)

        mock_settings = MagicMock()
        mock_settings.param_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            # Load initial registry
            await registry.load_param_registry()

            # Add another param file
            param_path2 = Path(tmp_dir) / "params2.yaml"
            param_path2.write_text(param_yaml2)

            # get_param_set should reload and find the new param set
            params = await registry.get_param_set("params2")
            assert params.id == "params2"


@pytest.mark.asyncio
async def test_load_workflow_registry_empty_dir(reset_registries):
    """Test loading workflow registry from empty directory."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        mock_settings = MagicMock()
        mock_settings.workflow_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            reg = await registry.load_workflow_registry()
            assert reg == {}


@pytest.mark.asyncio
async def test_load_param_registry_empty_dir(reset_registries):
    """Test loading param registry from empty directory."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        mock_settings = MagicMock()
        mock_settings.param_dir = tmp_dir

        with patch.object(registry, "get_settings", return_value=mock_settings):
            reg = await registry.load_param_registry()
            assert reg == {}
