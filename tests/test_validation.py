import hashlib
from io import BytesIO

import pytest
from starlette.datastructures import UploadFile

from app.models import FileKind
from app.validation import (
    EmptyUpload,
    UnsupportedFileType,
    UploadTooLarge,
    sanitize_filename,
    sniff_type,
    validate_upload,
)
from tests.helpers import make_image, make_pdf


def upload(data: bytes, filename: str = "file.bin") -> UploadFile:
    return UploadFile(file=BytesIO(data), filename=filename)


@pytest.mark.parametrize(
    ("data", "mime", "kind"),
    [
        (make_image(fmt="JPEG"), "image/jpeg", FileKind.IMAGE),
        (make_image(fmt="PNG"), "image/png", FileKind.IMAGE),
        (make_image(fmt="WEBP"), "image/webp", FileKind.IMAGE),
        (make_image(fmt="GIF"), "image/gif", FileKind.IMAGE),
        (make_pdf(["hello"]), "application/pdf", FileKind.PDF),
    ],
)
def test_sniff_accepts_allowed_types(data, mime, kind):
    assert sniff_type(data[:261]) == (mime, kind)


@pytest.mark.parametrize(
    "data",
    [
        b"just some text",
        b"MZ\x90\x00" + b"\x00" * 100,  # Windows executable
        b"PK\x03\x04" + b"\x00" * 100,  # zip
    ],
)
def test_sniff_rejects_other_types(data):
    with pytest.raises(UnsupportedFileType):
        sniff_type(data)


async def test_validate_upload_hashes_and_measures():
    data = make_image()
    result = await validate_upload(upload(data, "photo.jpg"), max_bytes=10_000_000)
    with result.fileobj:
        assert result.fileobj.read() == data
    assert result.size == len(data)
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert result.content_type == "image/jpeg"
    assert result.filename == "photo.jpg"


async def test_validate_upload_ignores_misleading_extension():
    exe = b"MZ\x90\x00" + b"\x00" * 500
    with pytest.raises(UnsupportedFileType):
        await validate_upload(upload(exe, "cute-cat.jpg"), max_bytes=10_000_000)


async def test_validate_upload_trusts_bytes_over_extension():
    result = await validate_upload(upload(make_image(fmt="PNG"), "actually.jpg"), 10_000_000)
    result.fileobj.close()
    assert result.content_type == "image/png"


async def test_validate_upload_too_large():
    with pytest.raises(UploadTooLarge):
        await validate_upload(upload(make_image()), max_bytes=100)


async def test_validate_upload_empty():
    with pytest.raises(EmptyUpload):
        await validate_upload(upload(b""), max_bytes=100)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("photo.jpg", "photo.jpg"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\report.pdf", "report.pdf"),
        ("my <script>.png", "my _script_.png"),
        ("...", "upload"),
        ("", "upload"),
        (None, "upload"),
        ("a" * 300 + ".jpg", "a" * 255),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected
