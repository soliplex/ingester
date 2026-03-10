import logging

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Response
from fastapi import status

from soliplex.ingester.lib.auth import get_current_user
from soliplex.ingester.lib.wf import operations as wf_ops

logger = logging.getLogger(__name__)

stats_router = APIRouter(prefix="/api/v1/stats", tags=["stats"], dependencies=[Depends(get_current_user)])


@stats_router.get(
    "/durations",
    status_code=status.HTTP_200_OK,
    summary="get workflow durations by run_group_id",
)
async def get_run_group_durations(run_group_id: int, response: Response):
    try:
        return await wf_ops.get_run_group_durations(run_group_id)
    except Exception as e:
        logger.exception("error getting run group durations", exc_info=e)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": str(e)}


@stats_router.get(
    "/step-stats",
    status_code=status.HTTP_200_OK,
    summary="get workflow step stats by run_group_id",
)
async def get_run_group_step_stats(run_group_id: int, response: Response):
    try:
        return await wf_ops.get_step_stats(run_group_id)
    except Exception as e:
        logger.exception("error getting run group step stats", exc_info=e)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": str(e)}


@stats_router.get(
    "/run-group-details/{run_group_id}",
    status_code=status.HTTP_200_OK,
    summary="get aggregated run group step details (PostgreSQL only)",
)
async def get_run_group_details(run_group_id: int, response: Response):
    """
    Returns aggregated step statistics for a run group grouped by
    batch_name, param_definition_id, step_type, and status.
    Includes document page counts (PostgreSQL only).
    """
    try:
        return await wf_ops.get_run_group_details(run_group_id)
    except RuntimeError as e:
        response.status_code = status.HTTP_501_NOT_IMPLEMENTED
        return {"error": str(e)}
    except Exception as e:
        logger.exception("error getting run group details", exc_info=e)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": str(e)}
