import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app

# Create in-memory SQLite for testing
engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    echo=False,
)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine)


async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session


@pytest.fixture(autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app.dependency_overrides[get_db] = override_get_db

    yield

    # Teardown
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()


def override_get_settings():
    settings = get_settings()
    settings.dev_token = "dev-token"
    settings.debug = True
    return settings


app.dependency_overrides[get_settings] = override_get_settings
get_settings().dev_token = "dev-token"
get_settings().debug = True
