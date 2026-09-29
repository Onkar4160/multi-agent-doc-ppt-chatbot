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


def run_migrations_sync(connection) -> None:
    """Safe, idempotent migration to add new columns to existing SQLite tables."""
    from sqlalchemy import text
    try:
        # Check artifacts table
        res = connection.execute(text("PRAGMA table_info(artifacts)"))
        cols = [row[1] for row in res.fetchall()]
        if cols:
            if "session_id" not in cols:
                connection.execute(text("ALTER TABLE artifacts ADD COLUMN session_id VARCHAR(100)"))
            if "run_id" not in cols:
                connection.execute(text("ALTER TABLE artifacts ADD COLUMN run_id VARCHAR(100)"))

        # Check chat_messages table
        res_msg = connection.execute(text("PRAGMA table_info(chat_messages)"))
        cols_msg = [row[1] for row in res_msg.fetchall()]
        if cols_msg:
            if "meta_json" not in cols_msg:
                connection.execute(text("ALTER TABLE chat_messages ADD COLUMN meta_json TEXT"))

        # Check artifact_versions table
        res_av = connection.execute(text("PRAGMA table_info(artifact_versions)"))
        cols_av = [row[1] for row in res_av.fetchall()]
        if cols_av:
            if "sources_json" not in cols_av:
                connection.execute(text("ALTER TABLE artifact_versions ADD COLUMN sources_json TEXT"))

        # Backfill sources_json for versions missing it
        try:
            import json
            import datetime
            today_str = datetime.date.today().strftime("%B %Y")
            null_versions = connection.execute(
                text("SELECT id, artifact_id, project_id, source_ids_json FROM artifact_versions WHERE sources_json IS NULL")
            ).fetchall()
            for av_id, art_id, proj_id, sids_json in null_versions:
                sources_map = {}
                art_row = connection.execute(
                    text("SELECT run_id FROM artifacts WHERE id = :aid"), {"aid": art_id}
                ).fetchone()
                run_id = art_row[0] if art_row else None

                # 1. Try finding Source rows matching run_id in metadata_json
                source_rows = []
                if run_id:
                    source_rows = connection.execute(
                        text("SELECT id, kind, url, title, metadata_json, created_at FROM sources WHERE metadata_json LIKE :pat"),
                        {"pat": f"%{run_id}%"}
                    ).fetchall()

                # 2. If none found, try matching by source_ids_json or project_id
                if not source_rows and sids_json:
                    try:
                        sids = json.loads(sids_json)
                        if sids:
                            placeholders = ",".join(str(int(s)) for s in sids)
                            source_rows = connection.execute(
                                text(f"SELECT id, kind, url, title, metadata_json, created_at FROM sources WHERE id IN ({placeholders})")
                            ).fetchall()
                    except Exception:
                        pass

                if not source_rows and proj_id:
                    source_rows = connection.execute(
                        text("SELECT id, kind, url, title, metadata_json, created_at FROM sources WHERE project_id = :pid"),
                        {"pid": proj_id}
                    ).fetchall()

                for s_id, s_kind, s_url, s_title, s_meta, s_created in source_rows:
                    cid = s_id
                    if s_meta:
                        try:
                            m = json.loads(s_meta)
                            if "citation_id" in m:
                                cid = m["citation_id"]
                        except Exception:
                            pass
                    dt_str = s_created.strftime("%B %Y") if hasattr(s_created, "strftime") else today_str
                    url_or_fn = s_url or ""
                    title = s_title or url_or_fn or f"Source {cid}"
                    sources_map[str(cid)] = {
                        "id": int(cid),
                        "title": title,
                        "url_or_filename": url_or_fn,
                        "kind": s_kind or "web",
                        "accessed_at": dt_str,
                    }

                if sources_map:
                    connection.execute(
                        text("UPDATE artifact_versions SET sources_json = :sjson WHERE id = :avid"),
                        {"sjson": json.dumps(sources_map), "avid": av_id}
                    )
        except Exception as b_exc:
            import logging
            logging.getLogger(__name__).warning("Sources backfill warning: %s", b_exc)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Migration warning: %s", exc)


async def create_tables():
    """Create all tables and run safe startup migrations."""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(run_migrations_sync)


def get_sync_session():
    """Return a synchronous SQLAlchemy Session instance."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    settings = get_settings()
    sync_url = settings.database_url.replace("sqlite+aiosqlite:", "sqlite:")
    sync_engine = create_engine(sync_url)
    return sessionmaker(bind=sync_engine, autoflush=False, autocommit=False, expire_on_commit=False)()


def reset_db():
    """Reset cached engine and session factory."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None

