"""SQLAlchemy async engine, session factory, and declarative base."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

_engine = None
_session_factory = None


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


def _get_engine():
    """Return cached async engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        db_url = settings.database_url
        if db_url.startswith("sqlite:///"):
            db_url = "sqlite+aiosqlite:///" + db_url[len("sqlite:///"):]
        elif db_url.startswith("sqlite:") and not db_url.startswith("sqlite+aiosqlite:"):
            db_url = "sqlite+aiosqlite:" + db_url[len("sqlite:"):]
        _engine = create_async_engine(
            db_url,
            echo=False,
            future=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return cached async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=_get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db():
    """FastAPI dependency that yields an async DB session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_tables():
    """Create all tables (called once on startup)."""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_sync_session():
    """Return a synchronous SQLAlchemy Session instance."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    settings = get_settings()
    sync_url = settings.database_url.replace("sqlite+aiosqlite:", "sqlite:")
    sync_engine = create_engine(sync_url)
    return sessionmaker(bind=sync_engine)()


def reset_db():
    """Reset cached engine and session factory."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None

