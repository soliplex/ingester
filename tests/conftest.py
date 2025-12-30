"""
Shared pytest fixtures for soliplex_ingester tests.

All database fixtures are function-scoped to ensure complete test isolation.
Each test gets a fresh in-memory SQLite database.
"""

import pytest_asyncio

from soliplex.ingester.lib.models import Database


@pytest_asyncio.fixture(scope="function")
async def db():
    """
    Create a fresh in-memory database for each test.

    This fixture:
    - Resets any existing database state
    - Creates a new in-memory SQLite database
    - Initializes all tables
    - Yields for the test to run
    - Cleans up after the test

    Usage:
        @pytest.mark.asyncio
        async def test_something(db):
            # Database is ready, get_session() will work
            async with get_session() as session:
                ...
    """
    # Reset to a fresh in-memory database
    await Database.reset("sqlite+aiosqlite:///:memory:")
    yield Database
    # Cleanup after test
    await Database.close()


@pytest_asyncio.fixture
async def db_session(db):
    """
    Provide a database session for tests that need direct session access.

    Usage:
        @pytest.mark.asyncio
        async def test_something(db_session):
            # Use the session directly
            db_session.add(some_model)
            await db_session.flush()
    """
    async with Database.session() as session:
        yield session
