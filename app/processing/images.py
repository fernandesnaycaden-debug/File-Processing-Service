import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


class InvalidImage(Exception):
    """The file can't be processed as an image. Retrying won't help."""


@dataclass
class GeneratedThumbnail:
    size: int
    width: int
    height: int
    data: bytes


@dataclass
class ImageResult:
    width: int
    height: int
    thumbnails: list[GeneratedThumbnail]


def process_image(data: bytes, sizes: list[int], max_pixels: int) -> ImageResult:
    """Validate an image and render WebP thumbnails that fit inside size x size boxes.

    CPU-bound; call it from a thread. Thumbnails are never upscaled, are oriented by
    their EXIF tag, and carry no metadata (EXIF often includes GPS coordinates).
    """
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with warnings.catch_warnings():
            # Pillow only warns between 1x and 2x the limit; treat that as a bomb too.
            warnings.simplefilter("error", Image.DecompressionBombWarning)

            with Image.open(BytesIO(data)) as im:
                im.verify()  # structural check; the image is unusable afterwards

            with Image.open(BytesIO(data)) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode not in ("RGB", "RGBA"):
                    im = im.convert("RGBA")
                width, height = im.size

                thumbnails = []
                for size in sorted(set(sizes)):
                    thumb = im.copy()
                    thumb.thumbnail((size, size), Image.Resampling.LANCZOS)
                    thumb.info.clear()
                    buf = BytesIO()
                    thumb.save(buf, "WEBP", quality=82, method=4)
                    thumbnails.append(
                        GeneratedThumbnail(size, thumb.width, thumb.height, buf.getvalue())
                    )
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,  # raised by some plugins' verify()
        ValueError,
    ) as exc:
        raise InvalidImage(f"Invalid image: {exc}") from exc

    return ImageResult(width=width, height=height, thumbnails=thumbnails)
