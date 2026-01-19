"""Unit tests for sync state operations."""

from datetime import datetime

import pytest

from soliplex.ingester.lib import operations
from soliplex.ingester.lib.models import SyncState
from soliplex.ingester.lib.models import get_session


@pytest.mark.asyncio
async def test_get_sync_state_not_exists(db):
    """Test getting sync state that doesn't exist."""
    result = await operations.get_sync_state("gitea:admin:notfound")
    assert result is None


@pytest.mark.asyncio
async def test_get_sync_state_exists(db):
    """Test getting existing sync state."""
    # Create sync state
    async with get_session() as session:
        state = SyncState(
            source_id="gitea:admin:test",
            last_commit_sha="abc123",
            last_sync_date=datetime(2026, 1, 16, 10, 30, 0),
            branch="main",
            sync_metadata={"commits_processed": 5},
        )
        session.add(state)
        await session.flush()

    # Get it via operations
    result = await operations.get_sync_state("gitea:admin:test")

    assert result is not None
    assert result.source_id == "gitea:admin:test"
    assert result.last_commit_sha == "abc123"
    assert result.branch == "main"
    assert result.sync_metadata["commits_processed"] == 5


@pytest.mark.asyncio
async def test_update_sync_state_create_new(db):
    """Test creating new sync state via update."""
    result = await operations.update_sync_state(
        source_id="gitea:admin:newrepo",
        commit_sha="xyz789",
        branch="main",
        metadata={"commits_processed": 3, "files_changed": 10},
    )

    assert result.source_id == "gitea:admin:newrepo"
    assert result.last_commit_sha == "xyz789"
    assert result.branch == "main"
    assert result.sync_metadata["commits_processed"] == 3
    assert result.sync_metadata["files_changed"] == 10
    assert result.last_sync_date is not None

    # Verify it was persisted
    async with get_session() as session:
        from sqlmodel import select

        statement = select(SyncState).where(SyncState.source_id == "gitea:admin:newrepo")
        db_result = await session.exec(statement)
        db_state = db_result.first()
        assert db_state is not None
        assert db_state.last_commit_sha == "xyz789"


@pytest.mark.asyncio
async def test_update_sync_state_update_existing(db):
    """Test updating existing sync state."""
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
        await session.flush()

    # Update it
    result = await operations.update_sync_state(
        source_id="gitea:admin:existing",
        commit_sha="new456",
        branch="develop",
        metadata={"commits_processed": 10, "files_changed": 15},
    )

    assert result.last_commit_sha == "new456"
    assert result.branch == "develop"
    # Metadata should be merged
    assert result.sync_metadata["commits_processed"] == 10
    assert result.sync_metadata["files_changed"] == 15

    # Verify only one record exists
    async with get_session() as session:
        from sqlmodel import select

        statement = select(SyncState).where(SyncState.source_id == "gitea:admin:existing")
        db_result = await session.exec(statement)
        states = db_result.all()
        assert len(states) == 1
        assert states[0].last_commit_sha == "new456"


@pytest.mark.asyncio
async def test_update_sync_state_merges_metadata(db):
    """Test that metadata is merged, not replaced."""
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
        await session.flush()

    # Update with new metadata
    result = await operations.update_sync_state(
        source_id="gitea:admin:merge",
        commit_sha="new456",
        branch="main",
        metadata={"new_key": "new_value", "commits_processed": 10},
    )

    # Both old and new keys should be present
    assert "old_key" in result.sync_metadata
    assert result.sync_metadata["old_key"] == "old_value"
    assert result.sync_metadata["new_key"] == "new_value"
    assert result.sync_metadata["commits_processed"] == 10  # Updated value


@pytest.mark.asyncio
async def test_update_sync_state_without_metadata(db):
    """Test updating sync state without providing metadata."""
    result = await operations.update_sync_state(
        source_id="gitea:admin:nometa", commit_sha="abc123", branch="main", metadata=None
    )

    assert result.last_commit_sha == "abc123"
    assert result.sync_metadata == {}


@pytest.mark.asyncio
async def test_update_sync_state_preserves_existing_metadata_when_none_provided(db):
    """Test that existing metadata is preserved when None is provided."""
    # Create initial state with metadata
    async with get_session() as session:
        state = SyncState(
            source_id="gitea:admin:preserve",
            last_commit_sha="old123",
            last_sync_date=datetime(2026, 1, 15, 10, 0, 0),
            branch="main",
            sync_metadata={"existing_key": "existing_value"},
        )
        session.add(state)
        await session.flush()

    # Update without metadata
    result = await operations.update_sync_state(
        source_id="gitea:admin:preserve", commit_sha="new456", branch="main", metadata=None
    )

    # Existing metadata should be preserved
    assert result.sync_metadata["existing_key"] == "existing_value"


@pytest.mark.asyncio
async def test_delete_sync_state_exists(db):
    """Test deleting existing sync state."""
    # Create sync state
    async with get_session() as session:
        state = SyncState(
            source_id="gitea:admin:todelete",
            last_commit_sha="abc123",
            last_sync_date=datetime(2026, 1, 16, 10, 0, 0),
            branch="main",
            sync_metadata={},
        )
        session.add(state)
        await session.flush()

    # Delete it
    await operations.delete_sync_state("gitea:admin:todelete")

    # Verify it's gone
    result = await operations.get_sync_state("gitea:admin:todelete")
    assert result is None


@pytest.mark.asyncio
async def test_delete_sync_state_not_exists(db):
    """Test deleting non-existent sync state raises error."""
    with pytest.raises(operations.SyncStateNotFoundError) as exc_info:
        await operations.delete_sync_state("gitea:admin:notfound")

    assert "gitea:admin:notfound" in str(exc_info.value)


@pytest.mark.asyncio
async def test_sync_state_workflow(db):
    """Test complete workflow: create, get, update, delete."""
    source_id = "gitea:admin:workflow"

    # 1. Get non-existent state
    result = await operations.get_sync_state(source_id)
    assert result is None

    # 2. Create new state
    created = await operations.update_sync_state(
        source_id=source_id, commit_sha="commit1", branch="main", metadata={"commits_processed": 1}
    )
    assert created.last_commit_sha == "commit1"

    # 3. Get existing state
    retrieved = await operations.get_sync_state(source_id)
    assert retrieved is not None
    assert retrieved.last_commit_sha == "commit1"
    assert retrieved.sync_metadata["commits_processed"] == 1

    # 4. Update state
    updated = await operations.update_sync_state(
        source_id=source_id, commit_sha="commit2", branch="main", metadata={"commits_processed": 2}
    )
    assert updated.last_commit_sha == "commit2"

    # 5. Verify update
    verified = await operations.get_sync_state(source_id)
    assert verified.last_commit_sha == "commit2"
    assert verified.sync_metadata["commits_processed"] == 2

    # 6. Delete state
    await operations.delete_sync_state(source_id)

    # 7. Verify deletion
    final = await operations.get_sync_state(source_id)
    assert final is None


@pytest.mark.asyncio
async def test_update_sync_state_updates_timestamp(db):
    """Test that update_sync_state updates the last_sync_date."""
    # Create initial state
    old_date = datetime(2026, 1, 1, 10, 0, 0)
    async with get_session() as session:
        state = SyncState(
            source_id="gitea:admin:timestamp",
            last_commit_sha="old123",
            last_sync_date=old_date,
            branch="main",
            sync_metadata={},
        )
        session.add(state)
        await session.flush()

    # Update it (this should update the timestamp)
    result = await operations.update_sync_state(
        source_id="gitea:admin:timestamp", commit_sha="new456", branch="main", metadata=None
    )

    # Timestamp should be newer than the old one
    assert result.last_sync_date > old_date


@pytest.mark.asyncio
async def test_sync_state_object_is_detached(db):
    """Test that returned SyncState objects are detached from session."""
    # Create state
    result = await operations.update_sync_state(
        source_id="gitea:admin:detached", commit_sha="abc123", branch="main", metadata={"key": "value"}
    )

    # Should be able to access attributes outside session
    assert result.source_id == "gitea:admin:detached"
    assert result.last_commit_sha == "abc123"
    assert result.sync_metadata["key"] == "value"

    # Get state
    retrieved = await operations.get_sync_state("gitea:admin:detached")
    assert retrieved is not None
    assert retrieved.source_id == "gitea:admin:detached"
    assert retrieved.sync_metadata["key"] == "value"
