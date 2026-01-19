"""Sync state management endpoints."""

import json
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Form
from fastapi import HTTPException

from ...lib import operations
from ...lib.auth import get_current_user

router = APIRouter(prefix="/api/v1/sync-state", tags=["sync"], dependencies=[Depends(get_current_user)])


@router.get("/{source_id}")
async def get_sync_state(source_id: str) -> dict[str, Any]:
    """
    Get last sync state for a source.

    Args:
        source_id: Source identifier (e.g., "gitea:admin:myrepo")

    Returns:
        SyncState object or default state if never synced

    Example:
        GET /api/v1/sync-state/gitea:admin:myrepo
    """
    state = await operations.get_sync_state(source_id)

    if not state:
        # Return default state for sources that haven't been synced yet
        return {
            "source_id": source_id,
            "last_commit_sha": None,
            "last_sync_date": None,
            "branch": "main",
            "sync_metadata": {},
        }

    return {
        "source_id": state.source_id,
        "last_commit_sha": state.last_commit_sha,
        "last_sync_date": state.last_sync_date.isoformat() if state.last_sync_date else None,
        "branch": state.branch,
        "sync_metadata": state.sync_metadata,
    }


@router.put("/{source_id}")
async def update_sync_state(
    source_id: str,
    commit_sha: str = Form(...),
    branch: str = Form("main"),
    metadata: str | None = Form(None),
) -> dict[str, Any]:
    """
    Update sync state after successful sync.

    Args:
        source_id: Source identifier
        commit_sha: Latest processed commit SHA
        branch: Branch name
        metadata: Optional sync metadata (JSON string)

    Returns:
        Updated sync state

    Example:
        PUT /api/v1/sync-state/gitea:admin:myrepo
        Form data:
            commit_sha=abc123
            branch=main
            metadata={"commits_processed": 5}
    """
    # Parse metadata JSON string if provided
    metadata_dict = json.loads(metadata) if metadata else None

    state = await operations.update_sync_state(source_id, commit_sha, branch, metadata_dict)

    return {
        "source_id": state.source_id,
        "last_commit_sha": state.last_commit_sha,
        "last_sync_date": state.last_sync_date.isoformat(),
        "branch": state.branch,
        "sync_metadata": state.sync_metadata,
    }


@router.delete("/{source_id}")
async def reset_sync_state(source_id: str) -> dict[str, str]:
    """
    Reset sync state (forces full sync on next run).

    Useful for recovery or testing.

    Args:
        source_id: Source identifier

    Returns:
        Confirmation message

    Raises:
        HTTPException: 404 if sync state not found

    Example:
        DELETE /api/v1/sync-state/gitea:admin:myrepo
    """
    try:
        await operations.delete_sync_state(source_id)
    except operations.SyncStateNotFoundError as err:
        raise HTTPException(status_code=404, detail=f"No sync state found for {source_id}") from err
    else:
        return {"message": f"Sync state reset for {source_id}"}
