"""ARQ worker: scan → process → update status/progress.

Run with:  arq app.worker.WorkerSettings
"""

import asyncio
import logging
import uuid
from typing import Any

from arq import Retry
from arq.connections import RedisSettings
from arq.worker import func
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db import SessionLocal, engine
from app.models import TERMINAL_STATUSES, File, FileKind, FileStatus, Thumbnail
from app.processing.images import InvalidImage, process_image
from app.processing.pdf import InvalidPdf, extract_text
from app.scanning import Scanner, get_scanner
from app.storage import Storage

log = logging.getLogger(__name__)

MAX_TRIES = 3

settings = get_settings()


async def startup(ctx: dict[str, Any]) -> None:
    logging.basicConfig(level=logging.INFO)
    ctx["settings"] = settings
    ctx["storage"] = Storage(settings)
    ctx["scanner"] = get_scanner(settings)
    ctx["sessionmaker"] = SessionLocal


async def shutdown(ctx: dict[str, Any]) -> None:
    await engine.dispose()


async def process_file(ctx: dict[str, Any], file_id: str) -> str:
    fid = uuid.UUID(file_id)
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    try:
        return await _run(ctx, fid)
    except (InvalidImage, InvalidPdf) as exc:
        # Bad input: retrying won't help.
        await _mark(sessionmaker, fid, status=FileStatus.FAILED, progress=100, error=str(exc))
        return FileStatus.FAILED
    except Exception as exc:
        # Probably transient (S3, DB or clamd hiccup): back off and retry.
        tries = ctx.get("job_try", 1)
        if tries < MAX_TRIES:
            log.warning("file %s: attempt %d failed (%r), retrying", fid, tries, exc)
            raise Retry(defer=tries * 5) from exc
        log.exception("file %s: giving up after %d attempts", fid, tries)
        await _mark(
            sessionmaker,
            fid,
            status=FileStatus.FAILED,
            progress=100,
            error=f"Processing failed after {tries} attempts: {exc}",
        )
        return FileStatus.FAILED


async def _run(ctx: dict[str, Any], fid: uuid.UUID) -> str:
    storage: Storage = ctx["storage"]
    scanner: Scanner = ctx["scanner"]
    cfg: Settings = ctx["settings"]

    async with ctx["sessionmaker"]() as session:
        file = await session.get(File, fid)
        if file is None:  # deleted before we got to it
            return "missing"
        if file.status in TERMINAL_STATUSES:
            return file.status

        await _save(session, file, status=FileStatus.SCANNING, progress=10, error=None)
        data = await storage.get_bytes(file.storage_key)
        await _save(session, file, progress=25)

        verdict = await scanner.scan(data)
        if not verdict.clean:
            await storage.delete_many([file.storage_key])
            await _save(
                session,
                file,
                status=FileStatus.QUARANTINED,
                progress=100,
                error=f"Malware detected: {verdict.signature}",
            )
            log.warning("file %s quarantined: %s", fid, verdict.signature)
            return FileStatus.QUARANTINED

        await _save(session, file, status=FileStatus.PROCESSING, progress=40)
        if file.kind == FileKind.IMAGE:
            await _process_image(session, file, data, storage, cfg)
        else:
            await _process_pdf(session, file, data, cfg)

        await _save(session, file, status=FileStatus.READY, progress=100)
        return FileStatus.READY


async def _process_image(
    session: AsyncSession, file: File, data: bytes, storage: Storage, cfg: Settings
) -> None:
    result = await asyncio.to_thread(process_image, data, cfg.thumbnail_sizes, cfg.max_image_pixels)
    await _save(session, file, width=result.width, height=result.height, progress=70)

    thumbnails = []
    for thumb in result.thumbnails:
        key = f"thumbnails/{file.owner_id}/{file.id}/{thumb.size}.webp"
        await storage.put_bytes(key, thumb.data, "image/webp")
        thumbnails.append(
            Thumbnail(size=thumb.size, width=thumb.width, height=thumb.height, storage_key=key)
        )

    # Replace rather than append, so a retried job doesn't hit the (file_id, size) unique key.
    file.thumbnails.clear()
    await session.flush()
    file.thumbnails.extend(thumbnails)
    await _save(session, file, progress=90)


async def _process_pdf(session: AsyncSession, file: File, data: bytes, cfg: Settings) -> None:
    result = await asyncio.to_thread(extract_text, data, cfg.pdf_max_pages)
    await _save(
        session, file, page_count=result.page_count, extracted_text=result.text, progress=90
    )


async def _save(session: AsyncSession, file: File, **changes: Any) -> None:
    """Apply changes and commit right away, so progress is visible to /events readers."""
    for name, value in changes.items():
        setattr(file, name, value)
    await session.commit()


async def _mark(
    sessionmaker: async_sessionmaker[AsyncSession], fid: uuid.UUID, **values: Any
) -> None:
    # A fresh session: the one used in _run may have been left mid-transaction.
    async with sessionmaker() as session:
        await session.execute(update(File).where(File.id == fid).values(**values))
        await session.commit()


class WorkerSettings:
    functions = [func(process_file, max_tries=MAX_TRIES)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    job_timeout = 300
    max_jobs = 4
