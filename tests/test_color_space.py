"""Tests für Farbraum-Umrechnung und Farbsuche."""

import numpy as np
import pytest

from lego_mosaik.domain.models import ColorDistance, PartCatalog, PartKind
from lego_mosaik.domain.palette import build_palette
from lego_mosaik.imaging import color_space
from lego_mosaik.imaging.color_space import (
    LabColorSpace,
    color_distances,
    palette_to_lab,
    rgb_to_lab,
)


def test_white_and_black_lightness() -> None:
    lab = rgb_to_lab([[255, 255, 255], [0, 0, 0]])
    assert lab[0, 0] == pytest.approx(100, abs=0.01)
    assert lab[1, 0] == pytest.approx(0, abs=0.01)


def test_round_trip_rgb_lab_rgb() -> None:
    rgb = np.array([[201.0, 26.0, 9.0], [0.0, 85.0, 191.0]])
    space = LabColorSpace(ColorDistance.CIE76)
    assert space.to_rgb(space.to_lab(rgb)) == pytest.approx(rgb, abs=0.01)


@pytest.mark.parametrize("distance", list(ColorDistance))
def test_palette_colors_map_to_themselves(catalog: PartCatalog, distance: ColorDistance) -> None:
    palette_lab = palette_to_lab(build_palette(catalog, (PartKind.PLATE,)))
    indices = LabColorSpace(distance).nearest(palette_lab, palette_lab)
    assert indices.tolist() == list(range(len(palette_lab)))


def test_distances_have_pixel_by_palette_shape() -> None:
    pixels, palette = rgb_to_lab(np.zeros((5, 3))), rgb_to_lab(np.ones((7, 3)))
    assert color_distances(pixels, palette, ColorDistance.CIEDE2000).shape == (5, 7)


def test_chunking_gives_same_result(monkeypatch: pytest.MonkeyPatch, catalog: PartCatalog) -> None:
    palette_lab = palette_to_lab(build_palette(catalog, (PartKind.PLATE,)))
    image = rgb_to_lab(np.random.default_rng(seed=1).integers(0, 256, (9, 7, 3)))
    space = LabColorSpace(ColorDistance.CIEDE2000)
    expected = space.nearest(image, palette_lab)
    monkeypatch.setattr(color_space, "PIXELS_PER_CHUNK", 5)  # viele kleine Blöcke
    assert np.array_equal(space.nearest(image, palette_lab), expected)
