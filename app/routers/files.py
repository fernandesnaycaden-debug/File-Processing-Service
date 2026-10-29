import asyncio
import json
import uuid

from arq import ArqRedis
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth import get_current_owner
from app.config import Settings, get_settings
from app.db import SessionLocal, get_session
from app.deps import get_queue, get_storage
from app.models import TERMINAL_STATUSES, File, FileKind, FileStatus, Owner
from app.schemas import FileList, FileOut, FileText, SignedUrl
from app.storage import Storage
from app.validation import UploadError, validate_upload

router = APIRouter(prefix="/files", tags=["files"])

EVENT_POLL_SECONDS = 0.5


async def get_owned_file(
    file_id: uuid.UUID,
    owner: Owner = Depends(get_current_owner),
    session: AsyncSession = Depends(get_session),
) -> File:
    file = await session.scalar(select(File).where(File.id == file_id, File.owner_id == owner.id))
    if file is None:
        # 404 rather than 403, so other owners' file IDs aren't confirmed to exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return file


def _require_ready(file: File) -> None:
    if file.status == FileStatus.QUARANTINED:
        raise HTTPException(status.HTTP_410_GONE, "File was quarantined by the virus scanner")
    if file.status != FileStatus.READY:
        raise HTTPException(status.HTTP_409_CONFLICT, f"File is not ready (status: {file.status})")


def _ttl(expires: int | None, settings: Settings) -> int:
    return min(expires or settings.signed_url_default_ttl, settings.signed_url_max_ttl)


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=FileOut)
async def upload_file(
    file: UploadFile,
    response: Response,
    owner: Owner = Depends(get_current_owner),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
    queue: ArqRedis = Depends(get_queue),
    settings: Settings = Depends(get_settings),
):
    """Upload an image (JPEG/PNG/WebP/GIF) or PDF. Processing happens in the background;
    poll `GET /files/{id}` or stream `GET /files/{id}/events` for progress."""
    try:
        upload = await validate_upload(file, settings.max_upload_bytes)
    except UploadError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc

    file_id = uuid.uuid4()
    key = f"originals/{owner.id}/{file_id}"
    with upload.fileobj:
        await storage.put(key, upload.fileobj, upload.content_type)

    record = File(
        id=file_id,
        owner_id=owner.id,
        filename=upload.filename,
        content_type=upload.content_type,
        kind=upload.kind,
        size=upload.size,
        sha256=upload.sha256,
        storage_key=key,
        status=FileStatus.PENDING,
        progress=0,
        thumbnails=[],
    )
    session.add(record)
    await session.commit()

    await queue.enqueue_job("process_file", str(file_id), _job_id=f"process:{file_id}")

    response.headers["Location"] = f"/files/{file_id}"
    return record


@router.get("", response_model=FileList)
async def list_files(
    status_: FileStatus | None = Query(None, alias="status"),
    kind: FileKind | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    owner: Owner = Depends(get_current_owner),
    session: AsyncSession = Depends(get_session),
):
    query = select(File).where(File.owner_id == owner.id)
    if status_ is not None:
        query = query.where(File.status == status_)
    if kind is not None:
        query = query.where(File.kind == kind)

    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(query.order_by(File.created_at.desc()).limit(limit).offset(offset))
    return FileList(
        items=[FileOut.model_validate(f) for f in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/{file_id}", response_model=FileOut)
async def get_file(file: File = Depends(get_owned_file)):
    return file


@router.get("/{file_id}/events")
async def file_events(request: Request, file: File = Depends(get_owned_file)):
    """Server-Sent Events stream of status/progress until processing finishes."""
    file_id = file.id

    async def stream():
        last = None
        while not await request.is_disconnected():
            # Fresh session per poll so we see the worker's latest commit.
            async with SessionLocal() as session:
                row = (
                    await session.execute(
                        select(File.status, File.progress, File.error).where(File.id == file_id)
                    )
                ).one_or_none()
            if row is None:
                yield {"event": "deleted", "data": "{}"}
                return

            state = {"status": row.status, "progress": row.progress, "error": row.error}
            if state != last:
                yield {"event": "progress", "data": json.dumps(state)}
                last = state
            if row.status in TERMINAL_STATUSES:
                return
            await asyncio.sleep(EVENT_POLL_SECONDS)

    return EventSourceResponse(stream())


@router.get("/{file_id}/download", response_model=SignedUrl)
async def download_url(
    expires: int | None = Query(None, ge=1, description="Seconds; capped by the server"),
    file: File = Depends(get_owned_file),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
):
    """Signed URL for the original file. Only issued once the file has passed scanning."""
    _require_ready(file)
    ttl = _ttl(expires, settings)
    return SignedUrl(url=storage.presign_get(file.storage_key, ttl, file.filename), expires_in=ttl)


@router.get("/{file_id}/thumbnails/{size}", response_model=SignedUrl)
async def thumbnail_url(
    size: int,
    expires: int | None = Query(None, ge=1),
    file: File = Depends(get_owned_file),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
):
    _require_ready(file)
    thumb = next((t for t in file.thumbnails if t.size == size), None)
    if thumb is None:
        available = [t.size for t in file.thumbnails]
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No {size}px thumbnail. Available: {available}"
        )
    ttl = _ttl(expires, settings)
    return SignedUrl(url=storage.presign_get(thumb.storage_key, ttl), expires_in=ttl)


@router.get("/{file_id}/text", response_model=FileText)
async def file_text(
    file: File = Depends(get_owned_file),
    session: AsyncSession = Depends(get_session),
):
    if file.kind != FileKind.PDF:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Text extraction only applies to PDFs")
    _require_ready(file)
    text = await session.scalar(select(File.extracted_text).where(File.id == file.id))
    return FileText(id=file.id, page_count=file.page_count, text=text or "")


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file: File = Depends(get_owned_file),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
):
    await storage.delete_many([file.storage_key, *(t.storage_key for t in file.thumbnails)])
    await session.delete(file)
    await session.commit()
