"""Image validation and preprocessing for the VLM request."""

import base64
import io
import math
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

register_heif_opener()

_ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF", "HEIC"}


class UnsupportedImageError(Exception):
    """Upload is not a decodable JPEG/PNG/WEBP/HEIC image."""


def load_image(data: bytes) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                fmt = img.format
                if fmt not in _ACCEPTED_FORMATS:
                    raise UnsupportedImageError(f"unsupported image format: {fmt}")
                img.load()
                img = ImageOps.exif_transpose(img)
                return img.convert("RGB")
    except UnsupportedImageError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError, SyntaxError, EOFError) as exc:
        raise UnsupportedImageError(f"cannot decode image: {type(exc).__name__}") from exc


def fit_max_pixels(img: Image.Image, max_pixels: int) -> Image.Image:
    w, h = img.size
    if w * h <= max_pixels:
        return img
    scale = math.sqrt(max_pixels / (w * h))
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    return img.resize((new_w, new_h), Image.LANCZOS)


def to_jpeg_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")
