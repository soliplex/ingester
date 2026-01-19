"""Sync state management endpoints."""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Form
from fastapi import HTTPException
from sqlmodel import select

from ...lib.auth import get_current_user
from ...lib.models import SyncState
from ...lib.models import get_session

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
    async with get_session() as session:
        statement = select(SyncState).where(SyncState.source_id == source_id)
        result = await session.exec(statement)
        state = result.first()

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
        metadata: Optional sync metadata

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

    async with get_session() as session:
        # Get existing state or create new
        statement = select(SyncState).where(SyncState.source_id == source_id)
        result = await session.exec(statement)
        state = result.first()

        if state:
            # Update existing
            state.last_commit_sha = commit_sha
            state.last_sync_date = datetime.now()
            state.branch = branch
            if metadata_dict:
                # Merge metadata
                state.sync_metadata.update(metadata_dict)
            session.add(state)
        else:
            # Create new
            state = SyncState(
                source_id=source_id,
                last_commit_sha=commit_sha,
                last_sync_date=datetime.now(),
                branch=branch,
                sync_metadata=metadata_dict or {},
            )
            session.add(state)

        await session.flush()

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
    async with get_session() as session:
        statement = select(SyncState).where(SyncState.source_id == source_id)
        result = await session.exec(statement)
        state = result.first()

        if not state:
            raise HTTPException(status_code=404, detail=f"No sync state found for {source_id}")

        await session.delete(state)
        await session.flush()

        return {"message": f"Sync state reset for {source_id}"}
