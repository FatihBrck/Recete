import base64
import io

import pytest
from PIL import Image

from reader.preprocess import (
    UnsupportedImageError,
    fit_max_pixels,
    load_image,
    to_jpeg_b64,
)


def _encode(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


def test_exif_rotation_applied():
    img = Image.new("RGB", (200, 100), "white")
    exif = Image.Exif()
    exif[0x0112] = 6
    data = _encode(img, "JPEG", exif=exif)
    assert load_image(data).size == (100, 200)


def test_heic_supported():
    import pillow_heif

    img = Image.new("RGB", (64, 48), "red")
    try:
        data = _encode(img, "HEIF")
    except Exception as exc:  # wheel without encoder
        pytest.skip(f"pillow-heif has no HEIF encoder: {exc}")
    assert pillow_heif is not None
    assert load_image(data).size == (64, 48)


def test_rgba_png_becomes_rgb():
    data = _encode(Image.new("RGBA", (20, 20), (255, 0, 0, 128)), "PNG")
    assert load_image(data).mode == "RGB"


def test_pdf_rejected():
    with pytest.raises(UnsupportedImageError):
        load_image(b"%PDF-1.4\n%fake pdf body\n")


def test_empty_rejected():
    with pytest.raises(UnsupportedImageError):
        load_image(b"")


def test_truncated_rejected():
    data = _encode(Image.effect_noise((300, 300), 80).convert("RGB"), "JPEG")
    with pytest.raises(UnsupportedImageError):
        load_image(data[: len(data) // 2])


def test_unaccepted_format_rejected():
    with pytest.raises(UnsupportedImageError):
        load_image(_encode(Image.new("RGB", (10, 10)), "GIF"))


def test_decompression_bomb_rejected(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    data = _encode(Image.new("RGB", (100, 100)), "PNG")
    with pytest.raises(UnsupportedImageError):
        load_image(data)


def test_fit_max_pixels_downscales():
    out = fit_max_pixels(Image.new("RGB", (4000, 3000)), 1_638_400)
    w, h = out.size
    assert w * h <= 1_638_400
    assert abs(w / h - 4 / 3) < 0.01


def test_no_upscale():
    out = fit_max_pixels(Image.new("RGB", (500, 400)), 1_638_400)
    assert out.size == (500, 400)


def test_jpeg_b64_roundtrip():
    s = to_jpeg_b64(Image.new("RGB", (123, 77), "blue"))
    assert not s.startswith("data:")
    assert Image.open(io.BytesIO(base64.b64decode(s))).size == (123, 77)
