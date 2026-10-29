import pytest

from app.processing.pdf import PAGE_SEPARATOR, InvalidPdf, extract_text
from tests.helpers import make_pdf


def test_extracts_text_per_page():
    result = extract_text(make_pdf(["Hello invoice", "Second page"]), max_pages=10)

    assert result.page_count == 2
    pages = result.text.split(PAGE_SEPARATOR)
    assert "Hello invoice" in pages[0]
    assert "Second page" in pages[1]


def test_respects_page_limit():
    result = extract_text(make_pdf(["one", "two", "three"]), max_pages=1)

    assert result.page_count == 3
    assert "one" in result.text
    assert "three" not in result.text


def test_rejects_garbage():
    with pytest.raises(InvalidPdf):
        extract_text(b"%PDF-1.4\nthis is not really a pdf", max_pages=10)
