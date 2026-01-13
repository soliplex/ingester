import logging
from pathlib import Path

import aiofiles
import aiofiles.os as aos
import yaml
from sqlmodel import select

from soliplex.ingester.lib.config import get_settings
from soliplex.ingester.lib.models import RunGroup
from soliplex.ingester.lib.models import WorkflowDefinition
from soliplex.ingester.lib.models import WorkflowParams
from soliplex.ingester.lib.models import get_session

logger = logging.getLogger(__name__)
_workflow_registry = None
_param_registry = None


async def load_workflow_definition(yaml_file: Path) -> WorkflowDefinition:
    async with aiofiles.open(yaml_file) as f:
        yaml_str = await f.read()
    loaded = yaml.safe_load(yaml_str)
    wf_loaded = WorkflowDefinition.model_validate(loaded)
    return wf_loaded


async def load_workflow_registry(
    force_reload: bool = False,
) -> dict[str, WorkflowDefinition]:
    global _workflow_registry
    if _workflow_registry is not None and not force_reload:
        return _workflow_registry
    settings = get_settings()
    reg = {}
    for p in Path(settings.workflow_dir).glob("*.yaml"):
        logger.debug(f"loading workflow {p}")
        wf = await load_workflow_definition(p)
        if wf.id in reg:
            msg = f"duplicate workflow id {wf.id}"
            raise ValueError(msg)
        reg[wf.id] = wf
    _workflow_registry = reg
    return reg


async def get_workflow_definition(
    wf_id: str | None = None,
) -> WorkflowDefinition:
    if wf_id is None:
        wf_id = get_default_workflow_id()
    registry = await load_workflow_registry()
    if wf_id in registry:
        return registry[wf_id]
    else:
        registry = await load_workflow_registry(force_reload=True)
        if wf_id in registry:
            return registry[wf_id]
        raise KeyError(f"workflow {wf_id} not found")


def get_default_workflow_id() -> str:
    settings = get_settings()
    return settings.default_workflow_id


def get_default_param_id() -> str:
    settings = get_settings()
    return settings.default_param_id


async def load_param_set(yaml_file: Path) -> WorkflowParams:
    async with aiofiles.open(yaml_file) as f:
        yaml_str = await f.read()
    loaded = yaml.safe_load(yaml_str)
    wf_loaded = WorkflowParams.model_validate(loaded)
    return wf_loaded


async def get_param_set(param_id: str | None = None) -> WorkflowParams:
    if param_id is None:
        param_id = get_default_param_id()
    registry = await load_param_registry()
    if param_id in registry:
        return registry[param_id]
    else:
        registry = await load_param_registry(force_reload=True)
        if param_id in registry:
            return registry[param_id]
        raise KeyError(f"param set {param_id} not found")


async def load_param_registry(
    force_reload: bool = False,
) -> dict[str, WorkflowParams]:
    global _param_registry
    if _param_registry is not None and not force_reload:
        return _param_registry
    settings = get_settings()
    reg = {}
    for p in Path(settings.param_dir).glob("*.yaml"):
        logger.debug(f"loading param set {p}")
        wf = await load_param_set(p)
        if wf.id in reg:
            raise ValueError(f"duplicate param set id {wf.id}")
        reg[wf.id] = wf
    _param_registry = reg
    return reg


async def save_param_set(param_set: WorkflowParams, overwrite: bool = False) -> Path:
    """
    Save a parameter set to YAML file.

    Parameters
    ----------
    param_set : WorkflowParams
        Parameter set to save
    overwrite : bool
        Whether to overwrite existing file

    Returns
    -------
    Path
        Path to saved file

    Raises
    ------
    ValueError
        If file exists and overwrite=False
    """
    settings = get_settings()
    param_dir = Path(settings.param_dir)

    # Use different naming for user-uploaded files
    if param_set.source == "user":
        filename = f"user_{param_set.id}.yaml"
    else:
        filename = f"{param_set.id}.yaml"

    file_path = param_dir / filename

    # Check for duplicates
    if file_path.exists() and not overwrite:
        raise ValueError(f"Parameter set '{param_set.id}' already exists")

    # Convert to dict and save as YAML
    param_dict = param_set.model_dump(exclude_none=True)
    yaml_content = yaml.safe_dump(param_dict, sort_keys=False)

    async with aiofiles.open(file_path, "w") as f:
        await f.write(yaml_content)

    # Clear registry cache to pick up new file
    global _param_registry
    _param_registry = None

    return file_path


async def delete_param_set(param_id: str) -> bool:
    """
    Delete a user-uploaded parameter set.

    Parameters
    ----------
    param_id : str
        ID of parameter set to delete

    Returns
    -------
    bool
        True if deleted, False if not found

    Raises
    ------
    ValueError
        If trying to delete built-in (source='app') parameter set,
        or if parameter is currently in use by any run groups
    """
    settings = get_settings()
    param_dir = Path(settings.param_dir)

    # Load registry to check source
    registry = await load_param_registry()
    if param_id not in registry:
        return False

    param_set = registry[param_id]
    if param_set.source != "user":
        raise ValueError(f"Cannot delete built-in parameter set '{param_id}'")

    # Check if parameter is in use by any run groups
    async with get_session() as session:
        statement = select(RunGroup).where(RunGroup.param_definition_id == param_id)
        result = await session.exec(statement)
        run_groups = result.all()

        if run_groups:
            raise ValueError(
                f"Cannot delete parameter set '{param_id}': it is currently used by {len(run_groups)} run group(s)"
            )

    # Delete file
    file_path = param_dir / f"user_{param_id}.yaml"
    if file_path.exists():
        await aos.unlink(file_path)

        # Clear registry cache
        global _param_registry
        _param_registry = None
        return True

    return False
