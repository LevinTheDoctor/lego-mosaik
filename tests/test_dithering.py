"""Tests für alle registrierten Dithering-Verfahren."""

import dataclasses

import numpy as np
import pytest
from conftest import color_blocks, gray_gradient, solid_image

from lego_mosaik.config import AppConfig
from lego_mosaik.dithering.error_diffusion import FLOYD_STEINBERG, ErrorDiffusionDitherer
from lego_mosaik.dithering.ordered import bayer_matrix
from lego_mosaik.domain.models import ColorDistance, FloatImage, PartCatalog, PartKind
from lego_mosaik.domain.palette import build_palette
from lego_mosaik.imaging.color_space import LabColorSpace, palette_to_lab, rgb_to_lab
from lego_mosaik.registry import DITHERERS

METHODS = sorted(DITHERERS)


@pytest.fixture
def palette_lab(catalog: PartCatalog) -> FloatImage:
    """Black, Blue, Red, White im Lab-Raum."""
    return palette_to_lab(build_palette(catalog, (PartKind.PLATE,)))


@pytest.mark.parametrize("method", METHODS)
def test_solid_image_gives_exactly_one_color(
    method: str, app_config: AppConfig, palette_lab: FloatImage
) -> None:
    ditherer = DITHERERS[method](app_config)
    grid = ditherer.dither(rgb_to_lab(solid_image((0, 85, 191))), palette_lab)
    assert grid.shape == (8, 8)
    assert set(grid.ravel().tolist()) == {1}  # Index 1 = Blue


@pytest.mark.parametrize("method", METHODS)
def test_two_runs_are_identical(
    method: str, app_config: AppConfig, palette_lab: FloatImage
) -> None:
    ditherer = DITHERERS[method](app_config)
    image = rgb_to_lab(gray_gradient())
    assert np.array_equal(ditherer.dither(image, palette_lab), ditherer.dither(image, palette_lab))


@pytest.mark.parametrize("method", METHODS)
def test_clear_blocks_stay_unchanged(
    method: str, app_config: AppConfig, palette_lab: FloatImage
) -> None:
    grid = DITHERERS[method](app_config).dither(rgb_to_lab(color_blocks()), palette_lab)
    assert grid[0, 0] == 2 and grid[0, 7] == 1 and grid[7, 0] == 3 and grid[7, 7] == 0


def _floyd(strength: float, serpentine: bool) -> ErrorDiffusionDitherer:
    space = LabColorSpace(ColorDistance.CIE76)
    return ErrorDiffusionDitherer(FLOYD_STEINBERG, space, strength=strength, serpentine=serpentine)


def test_serpentine_changes_pattern_deterministically(palette_lab: FloatImage) -> None:
    image = rgb_to_lab(gray_gradient(16))
    serpentine = _floyd(1.0, serpentine=True).dither(image, palette_lab)
    straight = _floyd(1.0, serpentine=False).dither(image, palette_lab)
    assert not np.array_equal(serpentine, straight)
    assert np.array_equal(serpentine, _floyd(1.0, serpentine=True).dither(image, palette_lab))


def test_zero_strength_equals_nearest(palette_lab: FloatImage) -> None:
    image = rgb_to_lab(gray_gradient(16))
    nearest = LabColorSpace(ColorDistance.CIE76).nearest(image, palette_lab)
    assert np.array_equal(_floyd(0.0, serpentine=True).dither(image, palette_lab), nearest)


def test_error_diffusion_preserves_average_brightness() -> None:
    black_white = rgb_to_lab([[0, 0, 0], [255, 255, 255]])
    image = rgb_to_lab(solid_image((128, 128, 128), size=32))  # mittleres Grau
    grid = _floyd(1.0, serpentine=True).dither(image, black_white)
    # Mittelgrau (L ≈ 54) ergibt etwa 54 % weiße Pixel statt nur einer Farbe
    assert 0.45 < grid.mean() < 0.65


def test_bayer_matrix_structure() -> None:
    # 2×2: Rangfolge [[0, 2], [3, 1]] → normiert ((r + 0.5) / 4) - 0.5
    assert bayer_matrix(2).tolist() == [[-0.375, 0.125], [0.375, -0.125]]
    eight = bayer_matrix(8)
    assert eight.shape == (8, 8)
    assert len(np.unique(eight)) == 64  # jeder Schwellwert genau einmal
    assert eight.mean() == pytest.approx(0)
    with pytest.raises(ValueError, match="Zweierpotenz"):
        bayer_matrix(3)


def test_ordered_without_spread_equals_nearest(
    app_config: AppConfig, palette_lab: FloatImage
) -> None:
    config = dataclasses.replace(
        app_config, dithering=dataclasses.replace(app_config.dithering, spread=0.0)
    )
    image = rgb_to_lab(gray_gradient(16))
    nearest = DITHERERS["nearest"](config).dither(image, palette_lab)
    assert np.array_equal(DITHERERS["ordered"](config).dither(image, palette_lab), nearest)
