"""Tests für Bildbearbeitung vor dem Mosaik: Drehen, Ausschnitt, Farbfilter, Hintergrund."""

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from lego_mosaik.domain.models import BackgroundMode, ColorFilter, FitMode, Resampling
from lego_mosaik.imaging.background import adjust_background, detect_background
from lego_mosaik.imaging.filters import apply_filter, transform
from lego_mosaik.imaging.preparer import ImageEdits, PillowImagePreparer

WHITE, RED, BLUE = (255, 255, 255), (201, 26, 9), (0, 85, 191)


def _subject_image(size: int = 64) -> Image.Image:
    """Weißer Hintergrund, rote Scheibe in der Mitte, darin ein weißes Quadrat (Teil des Motivs)."""
    image = Image.new("RGB", (size, size), WHITE)
    draw = ImageDraw.Draw(image)
    margin = size // 5
    draw.ellipse((margin, margin, size - margin, size - margin), fill=RED)
    center = size // 2
    draw.rectangle((center - 3, center - 3, center + 3, center + 3), fill=WHITE)
    return image


def test_transform_rotates_clockwise_mirrors_and_crops() -> None:
    image = Image.new("RGB", (40, 20), WHITE)
    image.putpixel((0, 0), RED)  # oben links
    rotated = transform(image, rotate=90, mirror=False, crop=None)
    assert rotated.size == (20, 40)
    assert rotated.getpixel((19, 0)) == RED  # im Uhrzeigersinn: oben links → oben rechts
    mirrored = transform(image, rotate=0, mirror=True, crop=None)
    assert mirrored.getpixel((39, 0)) == RED
    cropped = transform(image, rotate=0, mirror=False, crop=(0, 0, 50, 50))
    assert cropped.size == (20, 10)
    assert cropped.getpixel((0, 0)) == RED


@pytest.mark.parametrize("color_filter", list(ColorFilter))
def test_every_filter_keeps_size_and_mode(color_filter: ColorFilter) -> None:
    image = _subject_image()
    filtered = apply_filter(image, color_filter, 1.0)
    assert filtered.size == image.size and filtered.mode == "RGB"


def test_filters_change_colors_as_expected() -> None:
    image = Image.new("RGB", (4, 4), (100, 150, 200))
    gray = np.asarray(apply_filter(image, ColorFilter.GRAYSCALE, 1.0))
    assert (gray[..., 0] == gray[..., 1]).all() and (gray[..., 1] == gray[..., 2]).all()
    warm = apply_filter(image, ColorFilter.WARM, 1.0).getpixel((0, 0))
    assert isinstance(warm, tuple) and warm[0] > 100 and warm[2] < 200
    assert apply_filter(image, ColorFilter.INVERT, 1.0).getpixel((0, 0)) == (155, 105, 55)
    assert apply_filter(image, ColorFilter.SEPIA, 0.0) is image  # Stärke 0 = Original
    half = apply_filter(image, ColorFilter.INVERT, 0.5).getpixel((0, 0))
    assert isinstance(half, tuple) and all(120 <= value <= 135 for value in half)  # halb gemischt


def test_background_is_detected_from_the_border() -> None:
    info = detect_background(_subject_image(), tolerance=12)
    assert info.color == WHITE
    assert info.mask[0, 0] and info.mask[-1, -1]  # Ecken
    assert not info.mask[20, 32]  # rote Scheibe
    assert not info.mask[32, 32]  # weißes Quadrat im Motiv: nicht vom Rand erreichbar
    assert 0.3 < info.share < 0.7


def test_background_can_be_replaced_or_adjusted() -> None:
    image = _subject_image()
    mask = detect_background(image, tolerance=12).mask
    removed = adjust_background(image, mask, BackgroundMode.REMOVE, amount=0.5, color=BLUE)
    assert removed.getpixel((0, 0)) == BLUE and removed.getpixel((32, 20)) == RED
    darker = adjust_background(image, mask, BackgroundMode.DARKEN, amount=0.5, color=BLUE)
    corner = darker.getpixel((0, 0))
    assert isinstance(corner, tuple) and corner[0] < 200
    assert darker.getpixel((32, 20)) == RED  # Motiv bleibt
    kept = adjust_background(image, mask, BackgroundMode.KEEP, amount=0.5, color=BLUE)
    assert kept is image


def test_preparer_returns_background_mask_in_mosaic_size() -> None:
    preparer = PillowImagePreparer(
        fit_mode=FitMode.CROP,
        resampling=Resampling.BOX,
        edits=ImageEdits(background=BackgroundMode.REMOVE),
    )
    buffer = _png(_subject_image())
    prepared = preparer.prepare(buffer, width=16, height=16, pad_color=WHITE, background_color=BLUE)
    assert prepared.background is not None and prepared.background.shape == (16, 16)
    assert prepared.background[0, 0] and not prepared.background[5, 8]
    assert tuple(prepared.rgb[0, 0]) == BLUE
    plain = PillowImagePreparer(
        fit_mode=FitMode.CROP, resampling=Resampling.BOX, edits=ImageEdits()
    )
    assert plain.prepare(buffer, width=16, height=16, pad_color=WHITE).background is None


def test_inspect_reports_size_after_rotation_and_crop() -> None:
    preparer = PillowImagePreparer(
        fit_mode=FitMode.CROP,
        resampling=Resampling.BOX,
        edits=ImageEdits(rotate=90, crop=(0, 0, 100, 50)),
    )
    info = preparer.inspect(_png(Image.new("RGB", (40, 20), WHITE)))
    assert (info.width, info.height) == (20, 20)


def test_edited_preview_is_a_png() -> None:
    preparer = PillowImagePreparer(
        fit_mode=FitMode.CROP,
        resampling=Resampling.BOX,
        edits=ImageEdits(color_filter=ColorFilter.SEPIA),
    )
    png = preparer.edited(
        _png(_subject_image(200)), max_px=50, pad_color=WHITE, background_color=WHITE
    )
    assert png.startswith(b"\x89PNG")


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
