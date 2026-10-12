"""Tests für Laden und Einpassen von Bildern."""

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from lego_mosaik.domain.errors import InvalidImageError
from lego_mosaik.domain.models import FitMode, Resampling
from lego_mosaik.imaging.fitting import enhance, fit_image
from lego_mosaik.imaging.loader import load_image

WHITE = (255, 255, 255)
EXIF_ORIENTATION_TAG = 0x0112
ROTATE_90_CW = 6  # EXIF-Wert: Bild muss um 90° im Uhrzeigersinn gedreht angezeigt werden


def _png_bytes(image: Image.Image, **save_options: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", **save_options)
    return buffer.getvalue()


def test_exif_rotation_is_applied() -> None:
    exif = Image.Exif()
    exif[EXIF_ORIENTATION_TAG] = ROTATE_90_CW
    data = _png_bytes(Image.new("RGB", (4, 2), (255, 0, 0)), exif=exif)
    assert load_image(data, WHITE).size == (2, 4)


def test_transparency_uses_background() -> None:
    image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    image.putpixel((1, 0), (255, 0, 0, 255))
    loaded = load_image(_png_bytes(image), (0, 85, 191))
    assert loaded.mode == "RGB"
    assert loaded.getpixel((0, 0)) == (0, 85, 191)
    assert loaded.getpixel((1, 0)) == (255, 0, 0)


def test_invalid_data_raises() -> None:
    with pytest.raises(InvalidImageError, match="kein lesbares Bild"):
        load_image(b"kein bild", WHITE)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(InvalidImageError, match="nicht gefunden"):
        load_image(tmp_path / "fehlt.png", WHITE)


def test_crop_keeps_center() -> None:
    # 30 × 10: links rot, Mitte blau, rechts rot → CROP auf 10 × 10 behält nur die blaue Mitte
    image = Image.new("RGB", (30, 10), (255, 0, 0))
    image.paste((0, 0, 255), (10, 0, 20, 10))
    fitted = fit_image(
        image,
        width=10,
        height=10,
        fit_mode=FitMode.CROP,
        pad_color=WHITE,
        resampling=Resampling.BOX,
    )
    assert fitted.size == (10, 10)
    assert np.unique(np.asarray(fitted).reshape(-1, 3), axis=0).tolist() == [[0, 0, 255]]


def test_pad_fills_border_with_pad_color() -> None:
    image = Image.new("RGB", (20, 10), (0, 0, 0))
    fitted = fit_image(
        image, width=8, height=8, fit_mode=FitMode.PAD, pad_color=WHITE, resampling=Resampling.BOX
    )
    pixels = np.asarray(fitted)
    assert fitted.size == (8, 8)
    assert tuple(pixels[0, 0]) == WHITE  # oberer Rand
    assert tuple(pixels[4, 4]) == (0, 0, 0)  # Bildmitte


def test_enhance_neutral_returns_same_image() -> None:
    image = Image.new("RGB", (2, 2), (10, 20, 30))
    assert enhance(image, contrast=1.0, saturation=1.0) is image
    gray = enhance(image, contrast=1.0, saturation=0.0)
    red, green, blue = gray.getpixel((0, 0))  # type: ignore[misc]
    assert red == green == blue
