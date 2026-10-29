from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings, get_settings
from app.db import engine
from app.routers import admin, files, health
from app.storage import Storage

# Slack for multipart boundaries and headers on top of the file itself.
MULTIPART_OVERHEAD = 64 * 1024


class ContentLengthLimit:
    """Reject oversized requests from the Content-Length header, before the body is read.

    Starlette spools the whole multipart body before the endpoint runs, so this stops
    honest-but-huge uploads early. validate_upload() still enforces the limit on the
    actual bytes, which covers chunked requests that don't send Content-Length.
    """

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST":
            for name, value in scope["headers"]:
                if name == b"content-length" and value.isdigit() and int(value) > self.max_bytes:
                    response = JSONResponse({"detail": "File too large"}, status_code=413)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.storage = Storage(settings)
        app.state.arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        yield
        await app.state.arq.aclose()
        await engine.dispose()

    app = FastAPI(
        title="File Processing Service",
        version="0.1.0",
        description="Upload images and PDFs; scanning, thumbnails and text extraction run "
        "asynchronously. Authenticate with the `X-API-Key` header.",
        lifespan=lifespan,
    )
    app.add_middleware(ContentLengthLimit, max_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD)
    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(files.router)
    return app


app = create_app()
