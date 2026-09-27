import os
import random
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models import game  # noqa: F401  (registers tables)
from app.models.database import Base

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://localhost:5432/memorycard_test"
)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(database_url=TEST_DATABASE_URL, max_rounds=3, points_per_item=10, _env_file=None)


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def db(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
async def _clean_tables(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    yield
    if "engine" in request.fixturenames:
        engine: AsyncEngine = request.getfixturevalue("engine")
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE responses, rounds, sessions CASCADE"))


@pytest.fixture
def rng() -> random.Random:
    return random.Random(1234)


@pytest.fixture
async def redis():
    from fakeredis import FakeAsyncRedis

    client = FakeAsyncRedis(decode_responses=True)
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
def cache(redis, settings):
    from app.cache.redis import GameCache

    return GameCache(redis, game_ttl=settings.game_state_ttl, leaderboard_ttl=settings.leaderboard_ttl)


@pytest.fixture
async def client(session_factory, cache, settings):
    """HTTP client for the app, wired to the test database, fake Redis and test settings."""
    from httpx import ASGITransport, AsyncClient

    from app.core.config import get_settings
    from app.main import app
    from app.models.database import get_db

    async def _get_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.cache = cache
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()
