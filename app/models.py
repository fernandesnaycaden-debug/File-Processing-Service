import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class FileStatus(StrEnum):
    PENDING = "pending"
    SCANNING = "scanning"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    QUARANTINED = "quarantined"


TERMINAL_STATUSES = frozenset({FileStatus.READY, FileStatus.FAILED, FileStatus.QUARANTINED})


class FileKind(StrEnum):
    IMAGE = "image"
    PDF = "pdf"


def _str_enum(enum_cls: type[StrEnum]) -> Enum:
    # Stored as VARCHAR holding the enum *value* — no Postgres ENUM type to migrate.
    return Enum(
        enum_cls, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]
    )


def _now() -> datetime:
    # Python-side timestamps so attributes are never expired after a commit
    # (an expired attribute would trigger an implicit lazy load, which async sessions forbid).
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Owner(Base):
    __tablename__ = "owners"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class File(Base):
    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    kind: Mapped[FileKind] = mapped_column(_str_enum(FileKind))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(512))

    status: Mapped[FileStatus] = mapped_column(
        _str_enum(FileStatus), default=FileStatus.PENDING, index=True
    )
    progress: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)

    width: Mapped[int | None]
    height: Mapped[int | None]
    page_count: Mapped[int | None]
    # Potentially large; only loaded by the /text endpoint.
    extracted_text: Mapped[str | None] = mapped_column(Text, deferred=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    thumbnails: Mapped[list["Thumbnail"]] = relationship(
        back_populates="file",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Thumbnail.size",
    )


class Thumbnail(Base):
    __tablename__ = "thumbnails"
    __table_args__ = (UniqueConstraint("file_id", "size"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    size: Mapped[int]
    width: Mapped[int]
    height: Mapped[int]
    storage_key: Mapped[str] = mapped_column(String(512))

    file: Mapped[File] = relationship(back_populates="thumbnails")
