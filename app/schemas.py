import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import FileKind, FileStatus


class ThumbnailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    size: int
    width: int
    height: int


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    kind: FileKind
    size: int
    sha256: str
    status: FileStatus
    progress: int
    error: str | None
    width: int | None
    height: int | None
    page_count: int | None
    thumbnails: list[ThumbnailOut]
    created_at: datetime
    updated_at: datetime


class FileList(BaseModel):
    items: list[FileOut]
    total: int
    limit: int
    offset: int


class SignedUrl(BaseModel):
    url: str
    expires_in: int


class FileText(BaseModel):
    id: uuid.UUID
    page_count: int | None
    text: str


class OwnerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class OwnerCreated(BaseModel):
    id: uuid.UUID
    name: str
    api_key: str = Field(description="Shown once. Store it — only a hash is kept server-side.")
