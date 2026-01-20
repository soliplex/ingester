"""Unit tests for sync state routes."""

from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from soliplex.ingester.lib.models import SyncState
from soliplex.ingester.lib.models import get_session
from soliplex.ingester.server import app


@pytest.fixture
def client():
    """Create a test client with auth disabled."""
    with patch("soliplex.ingester.lib.auth.require_auth") as mock_require_auth:
        mock_require_auth.return_value = False
        yield TestClient(app)


@pytest.mark.asyncio
async def test_get_sync_state_not_exists(db, client):
    """Test getting sync state that doesn't exist."""
    response = client.get("/api/v1/sync-state/gitea:admin:nonexistent")

    assert response.status_code == 200
    data = response.json()
    assert data["source_id"] == "gitea:admin:nonexistent"
    assert data["last_commit_sha"] is None
    assert data["last_sync_date"] is None
    assert data["branch"] == "main"
    assert data["sync_metadata"] == {}


@pytest.mark.asyncio
async def test_get_sync_state_exists(db, client):
    """Test getting sync state that exists."""
    # Create sync state
    async with get_session() as session:
        state = SyncState(
            source_id="gitea:admin:test",
            last_commit_sha="abc123def456",
            last_sync_date=datetime(2026, 1, 16, 10, 30, 0),
            branch="main",
            sync_metadata={"commits_processed": 5},
        )
        session.add(state)
        await session.commit()

    response = client.get("/api/v1/sync-state/gitea:admin:test")

    assert response.status_code == 200
    data = response.json()
    assert data["source_id"] == "gitea:admin:test"
    assert data["last_commit_sha"] == "abc123def456"
    assert data["last_sync_date"] == "2026-01-16T10:30:00"
    assert data["branch"] == "main"
    assert data["sync_metadata"]["commits_processed"] == 5


@pytest.mark.asyncio
async def test_update_sync_state_new(db, client):
    """Test creating new sync state."""
    import json

    response = client.put(
        "/api/v1/sync-state/gitea:admin:newrepo",
        data={
            "commit_sha": "xyz789",
            "branch": "main",
            "metadata": json.dumps({"commits_processed": 3, "files_changed": 10}),
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source_id"] == "gitea:admin:newrepo"
    assert data["last_commit_sha"] == "xyz789"
    assert data["branch"] == "main"
    assert data["sync_metadata"]["commits_processed"] == 3
    assert data["sync_metadata"]["files_changed"] == 10

    # Verify in database
    async with get_session() as session:
        statement = select(SyncState).where(SyncState.source_id == "gitea:admin:newrepo")
        result = await session.exec(statement)
        state = result.first()
        assert state is not None
        assert state.last_commit_sha == "xyz789"


@pytest.mark.asyncio
async def test_update_sync_state_existing(db, client):
    """Test updating existing sync state."""
    import json

    # Create initial state
    async with get_session() as session:
        state = SyncState(
            source_id="gitea:admin:existing",
            last_commit_sha="old123",
            last_sync_date=datetime(2026, 1, 15, 10, 0, 0),
            branch="main",
            sync_metadata={"commits_processed": 5},
        )
        session.add(state)
        await session.commit()

    # Update it
    response = client.put(
        "/api/v1/sync-state/gitea:admin:existing",
        data={
            "commit_sha": "new456",
            "branch": "develop",
            "metadata": json.dumps({"commits_processed": 10, "files_changed": 15}),
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["last_commit_sha"] == "new456"
    assert data["branch"] == "develop"
    # Metadata should be merged
    assert data["sync_metadata"]["commits_processed"] == 10
    assert data["sync_metadata"]["files_changed"] == 15

    # Verify only one record exists
    async with get_session() as session:
        statement = select(SyncState).where(SyncState.source_id == "gitea:admin:existing")
        result = await session.exec(statement)
        states = result.all()
        assert len(states) == 1


@pytest.mark.asyncio
async def test_update_sync_state_merges_metadata(db, client):
    """Test that metadata is merged, not replaced."""
    import json

    # Create initial state with some metadata
    async with get_session() as session:
        state = SyncState(
            source_id="gitea:admin:merge",
            last_commit_sha="old123",
            last_sync_date=datetime(2026, 1, 15, 10, 0, 0),
            branch="main",
            sync_metadata={"old_key": "old_value", "commits_processed": 5},
        )
        session.add(state)
        await session.commit()

    # Update with new metadata
    response = client.put(
        "/api/v1/sync-state/gitea:admin:merge",
        data={
            "commit_sha": "new456",
            "branch": "main",
            "metadata": json.dumps({"new_key": "new_value", "commits_processed": 10}),
        },
    )

    assert response.status_code == 200
    data = response.json()
    # Both old and new keys should be present
    assert "old_key" in data["sync_metadata"]
    assert data["sync_metadata"]["old_key"] == "old_value"
    assert data["sync_metadata"]["new_key"] == "new_value"
    assert data["sync_metadata"]["commits_processed"] == 10  # Updated value


@pytest.mark.asyncio
async def test_reset_sync_state_exists(db, client):
    """Test resetting sync state that exists."""
    # Create sync state
    async with get_session() as session:
        state = SyncState(
            source_id="gitea:admin:toreset",
            last_commit_sha="abc123",
            last_sync_date=datetime(2026, 1, 16, 10, 0, 0),
            branch="main",
            sync_metadata={},
        )
        session.add(state)
        await session.commit()

    # Reset it
    response = client.delete("/api/v1/sync-state/gitea:admin:toreset")

    assert response.status_code == 200
    data = response.json()
    assert "reset" in data["message"].lower()
    assert "gitea:admin:toreset" in data["message"]

    # Verify it's gone from database
    async with get_session() as session:
        statement = select(SyncState).where(SyncState.source_id == "gitea:admin:toreset")
        result = await session.exec(statement)
        state = result.first()
        assert state is None


@pytest.mark.asyncio
async def test_reset_sync_state_not_exists(db, client):
    """Test resetting sync state that doesn't exist."""
    response = client.delete("/api/v1/sync-state/gitea:admin:notfound")

    assert response.status_code == 404
    data = response.json()
    assert "no sync state found" in data["detail"].lower()
    assert "gitea:admin:notfound" in data["detail"]


@pytest.mark.asyncio
async def test_sync_state_workflow(db, client):
    """Test complete workflow: create, get, update, reset."""
    import json

    source_id = "gitea:admin:workflow"

    # 1. Get non-existent state
    response = client.get(f"/api/v1/sync-state/{source_id}")
    assert response.status_code == 200
    assert response.json()["last_commit_sha"] is None

    # 2. Create new state
    response = client.put(
        f"/api/v1/sync-state/{source_id}",
        data={
            "commit_sha": "commit1",
            "branch": "main",
            "metadata": json.dumps({"commits_processed": 1}),
        },
    )
    assert response.status_code == 200
    assert response.json()["last_commit_sha"] == "commit1"

    # 3. Get existing state
    response = client.get(f"/api/v1/sync-state/{source_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["last_commit_sha"] == "commit1"
    assert data["sync_metadata"]["commits_processed"] == 1

    # 4. Update state
    response = client.put(
        f"/api/v1/sync-state/{source_id}",
        data={
            "commit_sha": "commit2",
            "branch": "main",
            "metadata": json.dumps({"commits_processed": 2}),
        },
    )
    assert response.status_code == 200
    assert response.json()["last_commit_sha"] == "commit2"

    # 5. Verify update
    response = client.get(f"/api/v1/sync-state/{source_id}")
    assert response.status_code == 200
    assert response.json()["last_commit_sha"] == "commit2"

    # 6. Reset state
    response = client.delete(f"/api/v1/sync-state/{source_id}")
    assert response.status_code == 200

    # 7. Verify reset
    response = client.get(f"/api/v1/sync-state/{source_id}")
    assert response.status_code == 200
    assert response.json()["last_commit_sha"] is None


@pytest.mark.asyncio
async def test_sync_state_datetime_format(db, client):
    """Test that datetime is properly formatted in ISO format."""
    # Create sync state with specific datetime
    async with get_session() as session:
        state = SyncState(
            source_id="gitea:admin:datetime",
            last_commit_sha="abc123",
            last_sync_date=datetime(2026, 1, 16, 14, 30, 45),
            branch="main",
            sync_metadata={},
        )
        session.add(state)
        await session.commit()

    response = client.get("/api/v1/sync-state/gitea:admin:datetime")

    assert response.status_code == 200
    data = response.json()
    assert data["last_sync_date"] == "2026-01-16T14:30:45"


@pytest.mark.asyncio
async def test_update_sync_state_without_metadata(db, client):
    """Test updating sync state without providing metadata."""
    response = client.put(
        "/api/v1/sync-state/gitea:admin:nometa",
        data={"commit_sha": "abc123", "branch": "main"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["last_commit_sha"] == "abc123"
    assert data["sync_metadata"] == {}
