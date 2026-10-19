"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.database import create_tables, fail_interrupted_runs, get_session_factory
from app.core.errors import AppError
from app.core.logging import configure_logging
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
    """Startup: logging, DB tables, interrupted runs, default workspace. Shutdown: nothing special."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    await create_tables()
    async with get_session_factory()() as session:
        await fail_interrupted_runs(session)
    session_factory = get_session_factory()
    async with session_factory() as session:
        await init_default_workspace(session)
    yield


app = FastAPI(
    title="Multi-Agent Doc & PPT Chatbot",
    version="0.1.0",
    lifespan=lifespan,
)

@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Errors from the taxonomy reach the client as their status and plain-language message."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


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

