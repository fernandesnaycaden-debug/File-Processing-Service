from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

PAGE_SEPARATOR = "\f"  # form feed, the conventional page break in extracted text


class InvalidPdf(Exception):
    """The file can't be read as a PDF. Retrying won't help."""


@dataclass
class PdfResult:
    page_count: int
    text: str


def extract_text(data: bytes, max_pages: int) -> PdfResult:
    """Extract text page by page. CPU-bound; call it from a thread."""
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise InvalidPdf("PDF is password protected")
        page_count = len(reader.pages)
        parts = [(page.extract_text() or "") for page in reader.pages[:max_pages]]
    except (PdfReadError, ValueError, KeyError) as exc:
        raise InvalidPdf(f"Invalid PDF: {exc}") from exc

    # Postgres TEXT columns reject NUL bytes, which some PDFs produce.
    text = PAGE_SEPARATOR.join(parts).replace("\x00", "")
    return PdfResult(page_count=page_count, text=text)
