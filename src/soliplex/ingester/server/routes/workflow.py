import logging

import yaml
from fastapi import APIRouter
from fastapi import Depends
from fastapi import Form
from fastapi import Response
from fastapi import status

from soliplex.ingester.lib import workflow as workflow
from soliplex.ingester.lib.auth import get_current_user
from soliplex.ingester.lib.models import PaginatedResponse
from soliplex.ingester.lib.models import RunGroup
from soliplex.ingester.lib.models import WorkflowParams
from soliplex.ingester.lib.models import WorkflowRun
from soliplex.ingester.lib.models import WorkflowRunWithDetails
from soliplex.ingester.lib.models import WorkflowStepType
from soliplex.ingester.lib.wf import operations as wf_ops
from soliplex.ingester.lib.wf import registry as wf_registry

logger = logging.getLogger(__name__)

wf_router = APIRouter(prefix="/api/v1/workflow", tags=["workflow"], dependencies=[Depends(get_current_user)])


@wf_router.get(
    "/",
    status_code=status.HTTP_200_OK,
    summary="get workflow runs with optional pagination",
)
async def get_workflows(
    batch_id: int | None = None,
    include_steps: bool = False,
    include_doc_info: bool = False,
    page: int | None = None,
    rows_per_page: int | None = None,
) -> (
    list[WorkflowRun]
    | list[WorkflowRunWithDetails]
    | PaginatedResponse[WorkflowRun]
    | PaginatedResponse[WorkflowRunWithDetails]
):
    """
    Get workflow runs with optional pagination.

    When page/rows_per_page not provided: Returns all rows as a list
    When page provided: Returns paginated response with metadata
    Results sorted by created_date descending (newest first)
    """
    # Validate pagination parameters
    if page is not None and page < 1:
        raise ValueError("page must be >= 1")

    # Set default rows_per_page if page is provided but rows_per_page is not
    if page is not None and rows_per_page is None:
        rows_per_page = 10

    # Validate rows_per_page
    if rows_per_page is not None and rows_per_page < 1:
        raise ValueError("rows_per_page must be >= 1")

    # Get data from operations layer
    items, total = await wf_ops.get_workflows(
        batch_id,
        include_steps=include_steps,
        include_doc_info=include_doc_info,
        page=page,
        rows_per_page=rows_per_page,
    )

    # If pagination not requested, return raw list (backward compatibility)
    if page is None and rows_per_page is None:
        return items

    # Calculate total_pages
    total_pages = (total + rows_per_page - 1) // rows_per_page if rows_per_page > 0 else 0

    # Return paginated response
    if include_steps or include_doc_info:
        return PaginatedResponse[WorkflowRunWithDetails](
            items=items,
            total=total,
            page=page or 1,
            rows_per_page=rows_per_page or 10,
            total_pages=total_pages,
        )
    else:
        return PaginatedResponse[WorkflowRun](
            items=items,
            total=total,
            page=page or 1,
            rows_per_page=rows_per_page or 10,
            total_pages=total_pages,
        )


@wf_router.get(
    "/by-status",
    status_code=status.HTTP_200_OK,
    summary="get workflow runs by status with optional pagination",
)
async def get_workflows_for_status(
    status: wf_ops.RunStatus,
    batch_id: int | None = None,
    include_doc_info: bool = False,
    page: int | None = None,
    rows_per_page: int | None = None,
) -> (
    list[WorkflowRun]
    | list[WorkflowRunWithDetails]
    | PaginatedResponse[WorkflowRun]
    | PaginatedResponse[WorkflowRunWithDetails]
):
    """
    Get workflow runs filtered by status with optional pagination.

    When page/rows_per_page not provided: Returns all rows as a list
    When page provided: Returns paginated response with metadata
    Results sorted by created_date descending (newest first)
    """
    # Validate pagination parameters
    if page is not None and page < 1:
        raise ValueError("page must be >= 1")

    # Set default rows_per_page if page is provided but rows_per_page is not
    if page is not None and rows_per_page is None:
        rows_per_page = 10

    # Validate rows_per_page
    if rows_per_page is not None and rows_per_page < 1:
        raise ValueError("rows_per_page must be >= 1")

    # Get data from operations layer
    items, total = await wf_ops.get_workflows_for_status(
        status,
        batch_id,
        include_doc_info=include_doc_info,
        page=page,
        rows_per_page=rows_per_page,
    )

    # If pagination not requested, return raw list (backward compatibility)
    if page is None and rows_per_page is None:
        return items

    # Calculate total_pages
    total_pages = (total + rows_per_page - 1) // rows_per_page if rows_per_page > 0 else 0

    # Return paginated response
    if include_doc_info:
        return PaginatedResponse[WorkflowRunWithDetails](
            items=items,
            total=total,
            page=page or 1,
            rows_per_page=rows_per_page or 10,
            total_pages=total_pages,
        )
    return PaginatedResponse[WorkflowRun](
        items=items,
        total=total,
        page=page or 1,
        rows_per_page=rows_per_page or 10,
        total_pages=total_pages,
    )


@wf_router.get("/definitions", summary="get workflow definitions")
async def list_workflows():
    wf = await wf_registry.load_workflow_registry()
    res = [{"id": x.id, "name": x.name} for x in wf.values()]
    return res


@wf_router.get(
    "/definitions/{workflow_id}",
    status_code=status.HTTP_200_OK,
    summary="get workflow definition by id",
)
async def get_workflow_def(workflow_id: str, response: Response):
    yaml_content = await wf_registry.get_workflow_definition_yaml_content(workflow_id)
    if yaml_content is not None:
        return Response(content=yaml_content, media_type="text/yaml")
    else:
        response.status_code = status.HTTP_404_NOT_FOUND
        return {"error": f"workflow definition {workflow_id} not found"}


@wf_router.get("/param-sets", summary="get param sets")
async def list_params():
    wf = await wf_registry.load_param_registry()
    res = [{"id": x.id, "name": x.name, "source": x.source} for x in wf.values()]
    return res


@wf_router.get("/param-sets/{set_id}", summary="get param set by id")
async def get_param_set(set_id: str, response: Response):
    yaml_content = await wf_registry.get_param_set_yaml_content(set_id)
    if yaml_content is not None:
        return Response(content=yaml_content, media_type="text/yaml")
    else:
        response.status_code = status.HTTP_404_NOT_FOUND
        return {"error": f"param set {set_id} not found"}


@wf_router.get(
    "/param_sets/target/{target}",
    summary="get param set by target lancedb file",
    status_code=status.HTTP_200_OK,
)
async def get_param_set_by_target(target: str, response: Response) -> list[WorkflowParams]:
    param_reg = await wf_registry.load_param_registry()
    ret = []
    for pset in param_reg.values():
        if WorkflowStepType.STORE in pset.config:
            store_step = pset.config[WorkflowStepType.STORE]
            if "data_dir" in store_step and store_step["data_dir"] == target:
                ret.append(pset)
    return ret


@wf_router.post(
    "/param-sets",
    status_code=status.HTTP_201_CREATED,
    summary="Upload new parameter set from YAML content",
)
async def upload_param_set(
    response: Response,
    yaml_content: str = Form(...),
) -> dict:
    """
    Create a new parameter set from YAML content.

    Parameters
    ----------
    yaml_content : str
        Raw YAML content as string

    Returns
    -------
    dict
        Success message with parameter set ID
    """
    try:
        # Parse YAML
        try:
            loaded = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            response.status_code = status.HTTP_400_BAD_REQUEST
            return {"error": f"Invalid YAML syntax: {str(e)}"}

        # Validate against Pydantic model
        try:
            param_set = WorkflowParams.model_validate(loaded)
        except Exception as e:
            response.status_code = status.HTTP_400_BAD_REQUEST
            return {"error": f"Invalid parameter set format: {str(e)}"}

        # Force source to 'user' for uploaded params
        param_set.source = "user"

        # Check for duplicate ID
        existing_registry = await wf_registry.load_param_registry()
        if param_set.id in existing_registry:
            response.status_code = status.HTTP_409_CONFLICT
            return {
                "error": f"Parameter set with ID '{param_set.id}' already exists",
                "existing_source": existing_registry[param_set.id].source,
            }

        # Save to file
        file_path = await wf_registry.save_param_set(param_set, overwrite=False)

        return {"message": "Parameter set created successfully", "id": param_set.id, "file_path": str(file_path)}

    except ValueError as e:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"error": str(e)}
    except Exception as e:
        logger.exception("Error uploading parameter set", exc_info=e)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": f"Internal server error: {str(e)}"}


@wf_router.delete(
    "/param-sets/{set_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a user-uploaded parameter set",
)
async def delete_param_set_endpoint(set_id: str, response: Response) -> dict:
    """
    Delete a user-uploaded parameter set.

    Only parameter sets with source='user' can be deleted.
    Built-in parameter sets cannot be deleted via API.
    Parameter sets currently in use by run groups cannot be deleted.
    """
    try:
        deleted = await wf_registry.delete_param_set(set_id)
        if deleted:
            return {"message": f"Parameter set '{set_id}' deleted successfully"}
        else:
            response.status_code = status.HTTP_404_NOT_FOUND
            return {"error": f"Parameter set '{set_id}' not found"}
    except ValueError as e:
        response.status_code = status.HTTP_403_FORBIDDEN
        return {"error": str(e)}
    except Exception as e:
        logger.exception("Error deleting parameter set", exc_info=e)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": str(e)}


@wf_router.get("/steps", status_code=status.HTTP_200_OK, summary="get run steps by status")
async def get_workflow_status(status: wf_ops.RunStatus):
    try:
        return await wf_ops.get_run_steps(status)
    except Exception as e:
        return {"error": str(e)}


@wf_router.get(
    "/run-groups",
    status_code=status.HTTP_200_OK,
    summary="get workflow run groups by batch_id(optional)",
)
async def get_workflow_run_groups(response: Response, batch_id: int | None = None) -> list[RunGroup]:
    try:
        return await wf_ops.get_run_groups_for_batch(batch_id)
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": str(e)}


@wf_router.get(
    "/run_groups/{run_group_id}",
    status_code=status.HTTP_200_OK,
    summary="get workflow run group by id",
)
async def get_workflow_run_group(run_group_id: int, response: Response):
    try:
        return await wf_ops.get_run_group(run_group_id)
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": str(e)}


@wf_router.delete(
    "/run_groups/{run_group_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a workflow run group and all dependent records",
)
async def delete_workflow_run_group(run_group_id: int, response: Response) -> dict:
    """
    Delete a workflow run group and all its dependent records.

    Performs cascading deletion of:
    - All RunStep records associated with WorkflowRuns in this group
    - All LifecycleHistory records for this group and its WorkflowRuns
    - All WorkflowRun records in this group
    - The RunGroup record itself

    Parameters
    ----------
    run_group_id : int
        The ID of the RunGroup to delete

    Returns
    -------
    dict
        Deletion statistics including counts of deleted records by type

    Notes
    -----
    - Works with both SQLite and PostgreSQL databases
    - The deletion is performed within a transaction and will be rolled back if any error occurs
    - Returns 404 if the RunGroup does not exist
    """
    try:
        result = await wf_ops.delete_run_group(run_group_id)
    except wf_ops.NotFoundError as e:
        response.status_code = status.HTTP_404_NOT_FOUND
        return {"error": str(e)}
    except RuntimeError as e:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"error": str(e)}
    except Exception as e:
        logger.exception("error deleting run group", exc_info=e)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": str(e)}
    else:
        return {
            "message": f"RunGroup {run_group_id} deleted successfully",
            "statistics": result,
        }


@wf_router.get(
    "/run_groups/{run_group_id}/stats",
    status_code=status.HTTP_200_OK,
    summary="get workflow runs by run_group_id",
)
async def get_run_group_stats(run_group_id: int, response: Response):
    try:
        return await wf_ops.get_run_group_stats(run_group_id)
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        logger.exception("error getting run group stats", exc_info=e)
        return {"error": str(e)}


@wf_router.get(
    "/runs",
    status_code=status.HTTP_200_OK,
    summary="get workflow runs by batch_id",
)
async def get_workflow_runs(batch_id: int):
    try:
        return await wf_ops.get_workflow_runs(batch_id)
    except Exception as e:
        return {"error": str(e)}


@wf_router.get(
    "/runs/{workflow_id}",
    status_code=status.HTTP_200_OK,
    summary="get workflow run by id",
)
async def get_workflow(workflow_id: int):
    try:
        result = await wf_ops.get_workflow_run(workflow_id, include_steps=True)
        # get_workflow_run returns a tuple (run, steps) when get_steps=True
        run, steps = result
        # Convert to dict and add steps
        run_dict = run.model_dump()
        run_dict["steps"] = [step.model_dump() for step in steps]

    except Exception as e:
        return {"error": str(e)}
    else:
        return run_dict


@wf_router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    summary="Start a new workflow run (one doc)",
)
async def start_workflow(
    response: Response,
    doc_id: str = Form(...),
    workflow_definiton_id: str | None = Form(None),
    param_id: str | None = Form(None),
    priority: int = Form(0),
):
    try:
        return await wf_ops.create_single_workflow_run(workflow_definiton_id, doc_id, priority=priority, param_id=param_id)
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        logger.exception("error starting workflow", exc_info=e)
        return {"error": str(e)}


@wf_router.post(
    "/retry",
    status_code=status.HTTP_201_CREATED,
    summary="Retry a failed workflow run for a run group",
)
async def retry_workflow(response: Response, run_group_id: int):
    try:
        return await wf_ops.reset_failed_steps(run_group_id)
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": str(e)}


@wf_router.get(
    "/runs/{workflow_id}/lifecycle",
    status_code=status.HTTP_200_OK,
    summary="get lifecycle history for workflow run",
)
async def get_workflow_lifecycle_history(workflow_id: int, response: Response):
    """
    Get lifecycle history events for a specific workflow run.

    Returns events ordered by start_date.
    """
    try:
        history = await wf_ops.get_lifecycle_history(workflow_run_id=workflow_id)
        return [record.model_dump() for record in history]
    except ValueError as e:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"error": str(e)}
    except Exception as e:
        logger.exception("error getting lifecycle history", exc_info=e)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": str(e)}
