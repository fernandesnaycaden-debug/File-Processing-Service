from io import BytesIO

import pytest
from PIL import Image

from app.processing.images import InvalidImage, process_image
from tests.helpers import make_image

LIMIT = 50_000_000


def test_generates_webp_thumbnails_preserving_aspect_ratio():
    result = process_image(make_image(800, 600), [128, 512], LIMIT)

    assert (result.width, result.height) == (800, 600)
    assert [(t.size, t.width, t.height) for t in result.thumbnails] == [
        (128, 128, 96),
        (512, 512, 384),
    ]
    with Image.open(BytesIO(result.thumbnails[0].data)) as im:
        assert im.format == "WEBP"
        assert im.size == (128, 96)


def test_never_upscales():
    result = process_image(make_image(50, 40), [128], LIMIT)
    assert (result.thumbnails[0].width, result.thumbnails[0].height) == (50, 40)


def test_applies_exif_orientation_and_strips_metadata():
    # Orientation 6 = stored sideways, display rotated 90°.
    result = process_image(make_image(200, 100, orientation=6), [128], LIMIT)

    assert (result.width, result.height) == (100, 200)
    with Image.open(BytesIO(result.thumbnails[0].data)) as im:
        assert im.size == (64, 128)
        assert len(im.getexif()) == 0


@pytest.mark.parametrize(("fmt", "mode"), [("PNG", "RGBA"), ("GIF", "P"), ("PNG", "L")])
def test_handles_other_modes(fmt, mode):
    result = process_image(make_image(300, 300, fmt=fmt, mode=mode), [128], LIMIT)
    assert result.thumbnails[0].width == 128


def test_rejects_corrupt_image():
    data = make_image()
    with pytest.raises(InvalidImage):
        process_image(data[: len(data) // 3], [128], LIMIT)


def test_rejects_decompression_bomb():
    with pytest.raises(InvalidImage):
        process_image(make_image(200, 200), [128], max_pixels=10_000)
