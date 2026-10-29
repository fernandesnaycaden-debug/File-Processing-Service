import hashlib
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from typing import BinaryIO

import filetype
from fastapi import UploadFile, status

from app.models import FileKind

ALLOWED_TYPES: dict[str, FileKind] = {
    "image/jpeg": FileKind.IMAGE,
    "image/png": FileKind.IMAGE,
    "image/webp": FileKind.IMAGE,
    "image/gif": FileKind.IMAGE,
    "application/pdf": FileKind.PDF,
}

SNIFF_BYTES = 261  # enough for every signature `filetype` knows about
CHUNK_SIZE = 1024 * 1024
SPOOL_MAX_MEMORY = 8 * 1024 * 1024

_UNSAFE_CHARS = re.compile(r"[^\w.\- ()]+")


class UploadError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class EmptyUpload(UploadError):
    pass


class UploadTooLarge(UploadError):
    status_code = 413  # Content Too Large (constant name differs across Starlette versions)


class UnsupportedFileType(UploadError):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


@dataclass
class ValidatedUpload:
    fileobj: BinaryIO  # positioned at 0; caller closes
    size: int
    sha256: str
    content_type: str
    kind: FileKind
    filename: str


def sniff_type(header: bytes) -> tuple[str, FileKind]:
    """Identify a file from its magic bytes. The client's Content-Type is never trusted."""
    guess = filetype.guess(header)
    if guess is None or guess.mime not in ALLOWED_TYPES:
        allowed = ", ".join(sorted(ALLOWED_TYPES))
        raise UnsupportedFileType(f"Unsupported file type. Allowed: {allowed}")
    return guess.mime, ALLOWED_TYPES[guess.mime]


def sanitize_filename(name: str | None) -> str:
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFKC", name)
    name = _UNSAFE_CHARS.sub("_", name).strip(" .")
    return name[:255] or "upload"


async def validate_upload(upload: UploadFile, max_bytes: int) -> ValidatedUpload:
    """Copy the upload into a spooled temp file, enforcing the size limit and hashing as we go."""
    out = tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_MEMORY)
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        while chunk := await upload.read(CHUNK_SIZE):
            size += len(chunk)
            if size > max_bytes:
                raise UploadTooLarge(f"File exceeds the {max_bytes} byte limit")
            if len(head) < SNIFF_BYTES:
                head += chunk[: SNIFF_BYTES - len(head)]
            digest.update(chunk)
            out.write(chunk)
        if size == 0:
            raise EmptyUpload("File is empty")
        content_type, kind = sniff_type(head)
    except BaseException:
        out.close()
        raise

    out.seek(0)
    return ValidatedUpload(
        fileobj=out,
        size=size,
        sha256=digest.hexdigest(),
        content_type=content_type,
        kind=kind,
        filename=sanitize_filename(upload.filename),
    )
