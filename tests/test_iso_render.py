"""Tests für den 3D-Renderer."""

import numpy as np
from PIL import Image

from lego_mosaik.outputs.iso_render import (
    HIGHLIGHT_RGB,
    Area,
    IsoView,
    RenderCell,
    edge_color,
    render_iso,
)

RED = (201, 26, 9)
GRAY = (160, 165, 169)


def _contains(image: Image.Image, rgb: tuple[int, int, int], tolerance: int = 6) -> bool:
    """True, wenn irgendein Pixel (fast) die Farbe ``rgb`` hat."""
    pixels = np.asarray(image).reshape(-1, 3).astype(int)
    return bool((np.abs(pixels - np.array(rgb)).sum(axis=1) <= tolerance).any())


def _small(cells: dict[tuple[int, int], RenderCell]) -> Image.Image:
    return render_iso(
        cells, Area(0, 0, 2, 2), IsoView(), baseplate_rgb=None, max_width=200, max_height=200
    )


def test_straight_view_keeps_rows_horizontal() -> None:
    view = IsoView(elevation_deg=60, azimuth_deg=0)
    left, right = view.project(0, 0, 0), view.project(100, 0, 0)
    assert left[1] == right[1]  # gleiche Zeile → gleiche Bildhöhe
    assert not view.shows_side_faces


def test_rotated_view_shows_side_faces() -> None:
    assert IsoView(elevation_deg=55, azimuth_deg=15).shows_side_faces


def test_nearer_cells_are_drawn_later() -> None:
    view = IsoView()
    assert view.depth(0, 40) > view.depth(0, 20)  # größere Zeile = näher an der Kamera


def test_render_fits_requested_size() -> None:
    cells = {(0, col): RenderCell(RED, part_id=1) for col in range(4)}
    image = render_iso(
        cells, Area(0, 0, 4, 4), IsoView(), baseplate_rgb=GRAY, max_width=300, max_height=300
    )
    assert image.width <= 300 and image.height <= 300
    assert _contains(image, RED)
    assert _contains(image, GRAY)  # Grundplatte sichtbar


def test_highlighted_parts_get_orange_outline() -> None:
    plain = {(0, 0): RenderCell(RED, part_id=1)}
    marked = {(0, 0): RenderCell(RED, part_id=1, highlighted=True)}
    assert not _contains(_small(plain), HIGHLIGHT_RGB)
    assert _contains(_small(marked), HIGHLIGHT_RGB)


def test_render_is_deterministic() -> None:
    cells = {(r, c): RenderCell(RED, part_id=r) for r in range(3) for c in range(3)}
    first = render_iso(
        cells, Area(0, 0, 3, 3), IsoView(), baseplate_rgb=GRAY, max_width=200, max_height=200
    )
    second = render_iso(
        cells, Area(0, 0, 3, 3), IsoView(), baseplate_rgb=GRAY, max_width=200, max_height=200
    )
    assert first.tobytes() == second.tobytes()


def test_edge_color_lightens_black() -> None:
    assert edge_color((5, 19, 29)) > (5, 19, 29)
    assert edge_color(RED) < RED
