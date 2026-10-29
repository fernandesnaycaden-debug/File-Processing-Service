import hashlib
import secrets

from fastapi import Depends, Header, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.models import Owner

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def generate_api_key() -> str:
    return "fps_" + secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    # Keys are 256 bits of randomness, so a fast hash is fine here (no need for bcrypt/argon2,
    # which exist to slow down guessing of low-entropy passwords). It also allows an indexed lookup.
    return hashlib.sha256(api_key.encode()).hexdigest()


async def get_current_owner(
    api_key: str | None = Security(api_key_header),
    session: AsyncSession = Depends(get_session),
) -> Owner:
    if api_key:
        owner = await session.scalar(
            select(Owner).where(Owner.api_key_hash == hash_api_key(api_key))
        )
        if owner is not None:
            return owner
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Missing or invalid API key",
        headers={"WWW-Authenticate": "ApiKey"},
    )


async def require_admin(
    x_admin_token: str = Header(),
    settings: Settings = Depends(get_settings),
) -> None:
    if not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid admin token")
