"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import create_tables, get_session_factory
from app.services.workspace_init import init_default_workspace
from app.api.health import router as health_router
from app.api.auth import router as auth_router
from app.api.files import router as files_router
from app.api.routes_artifacts import router as artifacts_router
from app.api.knowledge import router as knowledge_router
from app.api.chat import router as chat_router
from app.api.workspaces import router as workspaces_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create DB tables and initialize default workspace. Shutdown: nothing special."""
    await create_tables()
    session_factory = get_session_factory()
    async with session_factory() as session:
        await init_default_workspace(session)
    yield


app = FastAPI(
    title="Multi-Agent Doc & PPT Chatbot",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(files_router)
app.include_router(artifacts_router)
app.include_router(knowledge_router)
app.include_router(chat_router)
app.include_router(workspaces_router)

