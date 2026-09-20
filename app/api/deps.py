"""Shared FastAPI dependencies."""

from app.core.database import get_db
from app.core.security import get_current_user

__all__ = ["get_db", "get_current_user"]
