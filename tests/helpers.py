from io import BytesIO

from PIL import Image

# The standard, harmless antivirus test string. Every scanner flags it.
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def make_image(
    width: int = 800,
    height: int = 600,
    fmt: str = "JPEG",
    mode: str = "RGB",
    orientation: int | None = None,
) -> bytes:
    im = Image.new(mode, (width, height), color="teal" if mode == "RGB" else None)
    buf = BytesIO()
    kwargs = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        exif[0x010F] = "TestCam"  # Make — should not survive into thumbnails
        kwargs["exif"] = exif.tobytes()
    im.save(buf, fmt, **kwargs)
    return buf.getvalue()


def make_pdf(pages: list[str]) -> bytes:
    """Build a minimal valid PDF with one line of Helvetica text per page."""
    page_ids = [4 + 2 * i for i in range(len(pages))]
    objects: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] "
        f"/Count {len(pages)} >>",
        3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page_id, text in zip(page_ids, pages, strict=True):
        stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET"
        objects[page_id] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>"
        )
        objects[page_id + 1] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n{objects[num]}\nendobj\n".encode("latin-1")

    xref_at = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for num in range(1, size):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)
