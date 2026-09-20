"""Health check endpoint – no auth required."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Return service health status."""
    return {"status": "ok"}
