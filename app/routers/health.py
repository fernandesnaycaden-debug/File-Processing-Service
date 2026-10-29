from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import get_storage
from app.storage import Storage

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(
    request: Request,
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
):
    checks: dict[str, str] = {}

    async def check(name: str, probe) -> None:
        try:
            await probe()
            checks[name] = "ok"
        except Exception as exc:
            checks[name] = f"error: {type(exc).__name__}"

    await check("database", lambda: session.execute(text("SELECT 1")))
    await check("redis", lambda: request.app.state.arq.ping())
    await check("storage", storage.ping)

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status_code=200 if healthy else 503,
    )
