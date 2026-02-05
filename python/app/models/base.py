"""Base model and database utilities."""

from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, Integer
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from contextlib import asynccontextmanager

from app.config import get_settings


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


class TimestampMixin:
    """Mixin for created_at and updated_at timestamps."""

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


# Database engine and session factory
_engine = None
_session_factory = None


def get_engine():
    """Get or create async database engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        database_url = settings.database_url

        # Convert standard PostgreSQL URL to async format
        # Render provides postgresql:// but we need postgresql+asyncpg://
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)

        _engine = create_async_engine(
            database_url,
            echo=settings.debug,
            future=True
        )
    return _engine


def get_session_factory():
    """Get or create async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False
        )
    return _session_factory


async def init_db():
    """Initialize database - create tables if they don't exist.

    Uses create_all which is safe: it only creates tables that are missing.
    Existing tables and their data are preserved.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def get_db_session():
    """Get database session context manager."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db():
    """Dependency for FastAPI to get database session."""
    async with get_db_session() as session:
        yield session
