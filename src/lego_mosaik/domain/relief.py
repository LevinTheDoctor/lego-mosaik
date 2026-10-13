"""Relief wie bei LEGO Brick Sketches: Bildteile liegen auf Stützlagen höher.

Ergebnis ist ein Höhenraster: pro Zelle die Anzahl Plates unter dem sichtbaren Teil
(0 = direkt auf dem Unterbau). Reine Funktionen auf NumPy-Arrays, ohne I/O.
"""

from collections.abc import Sequence

lazy import numpy as np

from lego_mosaik.domain.editing import connected
from lego_mosaik.domain.models import EMPTY_CELL, BoolGrid, IndexGrid, LegoColor, ReliefMode

MAX_RELIEF_LEVELS = 3  # mehr Stufen werden instabil und die Anleitung unübersichtlich
# Gewichte für die wahrgenommene Helligkeit (ITU-R BT.601)
LUMA_WEIGHTS = (0.299, 0.587, 0.114)


def relief_levels(
    grid: IndexGrid,
    palette: Sequence[LegoColor],
    mode: ReliefMode,
    levels: int,
    background: BoolGrid | None = None,
) -> IndexGrid:
    """Höhe in Plates pro Zelle; Zellen mit ``EMPTY_CELL`` (z. B. Rahmen) bleiben bei 0.

    Args:
        grid: H×W-Raster mit Indizes in ``palette``.
        palette: Die Farben des Rasters.
        mode: Art des Reliefs.
        levels: Höchste Stufe in Plates (1 bis ``MAX_RELIEF_LEVELS``).
        background: Im Bild erkannter Hintergrund (für ``SUBJECT``); ``None`` = aus dem
            Raster bestimmen (``background_mask``).
    """
    heights = np.zeros(grid.shape, dtype=np.intp)
    used = grid != EMPTY_CELL
    if mode is ReliefMode.NONE or levels == 0 or not used.any():
        return heights
    if mode is ReliefMode.SUBJECT:
        flat = background_mask(grid) if background is None else background
        heights[used & ~flat] = levels
        return heights
    luma = np.array([_luma(color) for color in palette])[np.where(used, grid, 0)]
    if mode is ReliefMode.DARK:
        luma = -luma  # dunkel = hoch: die Reihenfolge einfach umdrehen
    # Grenzen bei gleichmäßigen Anteilen: jede Stufe bekommt etwa gleich viele Zellen
    bounds = np.quantile(luma[used], np.linspace(0, 1, levels + 2)[1:-1])
    heights[used] = np.searchsorted(bounds, luma[used], side="right")
    return heights


def background_mask(grid: IndexGrid) -> BoolGrid:
    """Hintergrund: die häufigste Randfarbe, soweit sie zusammenhängend vom Rand aus reicht.

    Ein Flutfüllen (Breitensuche über die vier Nachbarn) beginnt an allen Randzellen in
    dieser Farbe. Gleichfarbige Flächen mitten im Motiv bleiben so Teil des Motivs.
    """
    used = grid != EMPTY_CELL
    rows, cols = np.nonzero(used)
    top, bottom, left, right = rows.min(), rows.max(), cols.min(), cols.max()
    border = [
        (row, col)
        for row, col in zip(rows.tolist(), cols.tolist(), strict=True)
        if row in (top, bottom) or col in (left, right)
    ]
    colors = np.array([grid[cell] for cell in border])
    background = int(np.bincount(colors).argmax())
    return connected(grid == background, border)


def _luma(color: LegoColor) -> float:
    """Wahrgenommene Helligkeit 0–255."""
    return sum(channel * weight for channel, weight in zip(color.rgb, LUMA_WEIGHTS, strict=True))
