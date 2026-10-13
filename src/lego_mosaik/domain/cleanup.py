"""Aufräumen nach dem Dithering: seltene Farben und einzelne Pixel.

Fehlerdiffusion verteilt den Farbfehler auf die Nachbarn. Bei Fotos ergibt das weiche
Verläufe, bei Comic-Motiven mit klaren Flächen aber Rauschen: viele einzelne 1×1-Teile
und Farben, die nur ein- oder zweimal vorkommen (oft aus der Kantenglättung des Bilds).
"""

lazy import numpy as np

from lego_mosaik.domain.models import EMPTY_CELL, FloatImage, IndexGrid

# Die acht Nachbarn einer Zelle als (Zeilen-, Spaltenversatz)
NEIGHBORS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def rare_colors(grid: IndexGrid, palette_size: int, min_cells: int) -> list[int]:
    """Indizes der Farben, die auf weniger als ``min_cells`` Zellen vorkommen (auch 0)."""
    counts = np.bincount(grid[grid != EMPTY_CELL], minlength=palette_size)
    return [index for index, count in enumerate(counts.tolist()) if count < min_cells]


def remove_isolated(grid: IndexGrid, palette_lab: FloatImage) -> IndexGrid:
    """Ersetzt einzelne Pixel durch die häufigste Farbe ihrer acht Nachbarn.

    Einzeln heißt: Keiner der acht Nachbarn hat dieselbe Farbe. Linien (z. B. ein
    schwarzer Umriss, eine Noppe breit) bleiben erhalten, weil jede ihrer Zellen
    Nachbarn in derselben Farbe hat. Bei Gleichstand gewinnt die Nachbarfarbe, die der
    eigenen Farbe am ähnlichsten ist (kleinster Abstand im Lab-Raum).
    """
    height, width = grid.shape
    # np.pad: ein Rand aus leeren Zellen, damit auch Randzellen acht „Nachbarn“ haben
    padded = np.pad(grid, 1, constant_values=EMPTY_CELL)
    neighbors = np.stack(
        [padded[1 + dr : 1 + dr + height, 1 + dc : 1 + dc + width] for dr, dc in NEIGHBORS]
    )
    same = (neighbors == grid).sum(axis=0)
    isolated = (same == 0) & (grid != EMPTY_CELL) & ((neighbors != EMPTY_CELL).sum(axis=0) > 0)
    result = grid.copy()
    for row, col in zip(*np.nonzero(isolated), strict=True):
        around = neighbors[:, row, col]
        around = around[around != EMPTY_CELL]
        values, counts = np.unique(around, return_counts=True)
        best = values[counts == counts.max()]
        own = palette_lab[grid[row, col]]
        distances = np.linalg.norm(palette_lab[best] - own, axis=1)
        result[row, col] = best[int(np.argmin(distances))]
    return result
