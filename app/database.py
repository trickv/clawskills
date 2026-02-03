"""Database setup and session management."""

import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from .models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./clawskills.db")

# Handle in-memory databases for testing
connect_args = {}
poolclass = None
if "sqlite" in DATABASE_URL:
    connect_args = {"check_same_thread": False}
    if ":memory:" in DATABASE_URL:
        poolclass = StaticPool

engine = create_async_engine(
    DATABASE_URL,
    connect_args=connect_args,
    poolclass=poolclass,
    echo=False,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db():
    """Create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """Dependency to get database session."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
