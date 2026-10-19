"""JWT authentication and password hashing utilities."""

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db

_pwd_ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
_bearer = HTTPBearer()
_optional_bearer = HTTPBearer(auto_error=False)


def hash_password(plain: str) -> str:
    """Hash a plaintext password."""
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against its hash."""
    return _pwd_ctx.verify(plain, hashed)


def create_access_token(data: dict) -> str:
    """Create a signed JWT with expiration."""
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {**data, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT; raises on failure."""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
):
    """FastAPI dependency – extracts and validates the current user from JWT."""
    return await _user_from_token(creds.credentials, db)


async def get_user_from_header_or_token(
    token: str | None = Query(None, description="JWT for browser download links that cannot send headers"),
    creds: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    db: AsyncSession = Depends(get_db),
):
    """Like ``get_current_user`` but also accepts the JWT as a ``token`` query parameter (download links)."""
    raw = creds.credentials if creds else token
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return await _user_from_token(raw, db)


async def _user_from_token(raw: str, db: AsyncSession):
    from app.models.user import User  # deferred to avoid circular import

    payload = decode_access_token(raw)
    username: str | None = payload.get("sub")
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user
