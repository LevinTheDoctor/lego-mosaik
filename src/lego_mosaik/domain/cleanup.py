"""Aufräumen nach dem Dithering: seltene Farben, einzelne Pixel, kleine Flecken.

Fehlerdiffusion verteilt den Farbfehler auf die Nachbarn. Bei Fotos ergibt das weiche
Verläufe, bei Comic-Motiven mit klaren Flächen aber Rauschen: viele einzelne 1×1-Teile
und Farben, die nur ein- oder zweimal vorkommen (oft aus der Kantenglättung des Bilds).

Die Glättung legt kleine Flecken nur dann mit ihrer Umgebung zusammen, wenn die Farben
ähnlich sind. Ein dunkelbrauner Fleck im Schwarz verschwindet, eine schwarze Pupille im
weißen Auge bleibt. Die Farbabstände kommen als P×P-Tabelle herein, damit dieses Modul
den Farbraum nicht kennen muss.
"""

lazy import numpy as np
lazy import numpy.typing as npt
lazy from scipy import ndimage

from lego_mosaik.domain.models import EMPTY_CELL, FloatImage, IndexGrid

# Die acht Nachbarn einer Zelle als (Zeilen-, Spaltenversatz)
NEIGHBORS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
# 3×3 aus Einsen: Zellen gehören auch über Eck zusammen (8er-Nachbarschaft)
CONNECTIVITY = ((1, 1, 1), (1, 1, 1), (1, 1, 1))
MAX_MERGE_PASSES = 5  # durch Zusammenlegen können neue kleine Flecken entstehen


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


def majority_colors(sub_indices: IndexGrid, subsample: int, palette_size: int) -> IndexGrid:
    """Mehrheitsfarbe je Noppe aus einem feineren Raster.

    Statt die Farben einer Noppe zu mitteln (Kanten ergeben dann Mischfarben wie Olive
    Green zwischen Gelb und Schwarz), gewinnt die Farbe, die die meisten Teilpixel haben.

    Args:
        sub_indices: (H·s)×(W·s)-Raster mit Palettenindizes der Teilpixel.
        subsample: Teilpixel pro Noppe und Richtung (s).
        palette_size: Anzahl der Palettenfarben.

    Returns:
        H×W-Raster; bei Gleichstand gewinnt der kleinere Index.
    """
    height, width = sub_indices.shape[0] // subsample, sub_indices.shape[1] // subsample
    # (H, s, W, s) → (H, W, s·s): je Noppe alle Teilpixel nebeneinander
    blocks = (
        sub_indices[: height * subsample, : width * subsample]
        .reshape(height, subsample, width, subsample)
        .transpose(0, 2, 1, 3)
        .reshape(height * width, subsample * subsample)
    )
    # Versatz je Noppe: ein einziges bincount zählt alle Noppen auf einmal
    offsets = np.arange(height * width)[:, np.newaxis] * palette_size
    counts = np.bincount((blocks + offsets).ravel(), minlength=height * width * palette_size)
    return np.asarray(
        counts.reshape(height * width, palette_size).argmax(axis=1).reshape(height, width),
        dtype=np.intp,
    )


def merge_small_regions(
    grid: IndexGrid, distances: FloatImage, min_cells: int, max_delta_e: float
) -> IndexGrid:
    """Legt kleine Flecken mit ihrer Umgebung zusammen, wenn die Farben ähnlich sind.

    Ein Fleck ist eine zusammenhängende Fläche einer Farbe (auch über Eck). Hat sie
    weniger als ``min_cells`` Zellen, bekommt sie die häufigste Farbe ihres Rands, aber
    nur, wenn deren Abstand höchstens ``max_delta_e`` ist. Kontrastreiche Details wie
    Augen, Pupillen oder Lichtpunkte bleiben dadurch erhalten.

    Args:
        grid: H×W-Raster mit Palettenindizes (``EMPTY_CELL`` bleibt unverändert).
        distances: P×P-Tabelle der Farbabstände zwischen den Palettenfarben.
        min_cells: Flecken mit weniger Zellen kommen in Frage.
        max_delta_e: Größter Farbabstand zur Umgebung, bei dem noch zusammengelegt wird.
    """
    result = grid.copy()
    if min_cells <= 1:
        return result
    for _ in range(MAX_MERGE_PASSES):
        changed = False
        for color in np.unique(result[result != EMPTY_CELL]).tolist():
            labels, _count = ndimage.label(result == color, structure=CONNECTIVITY)
            # find_objects: je Fleck der kleinste umschließende Ausschnitt (slices)
            for number, box in enumerate(ndimage.find_objects(labels), start=1):
                if box is None:
                    continue
                changed |= _merge_region(
                    result, labels, number, box, color, distances, min_cells, max_delta_e
                )
        if not changed:
            break
    return result


def _merge_region(
    grid: IndexGrid,
    labels: npt.NDArray[np.signedinteger],
    number: int,
    box: tuple[slice, ...],
    color: int,
    distances: FloatImage,
    min_cells: int,
    max_delta_e: float,
) -> bool:
    """Färbt einen Fleck in ``grid`` um, falls er klein ist und ähnlich zur Umgebung."""
    # Ausschnitt um den Fleck, eine Zelle größer für den Rand (am Bildrand begrenzt)
    rows = slice(max(box[0].start - 1, 0), box[0].stop + 1)
    cols = slice(max(box[1].start - 1, 0), box[1].stop + 1)
    region = labels[rows, cols] == number
    if region.sum() >= min_cells:
        return False
    window = grid[rows, cols]
    ring = ndimage.binary_dilation(region, structure=CONNECTIVITY) & ~region
    around = window[ring & (window != EMPTY_CELL)]
    if around.size == 0:
        return False
    values, counts = np.unique(around, return_counts=True)
    best = values[counts == counts.max()]
    # Gleichstand: die Randfarbe, die der eigenen Farbe am ähnlichsten ist
    target = int(best[np.argmin(distances[color, best])])
    if distances[color, target] > max_delta_e:
        return False
    window[region] = target  # window ist ein View: ändert grid direkt
    return True


def majority_filter(
    grid: IndexGrid, distances: FloatImage, need: int, max_delta_e: float
) -> IndexGrid:
    """Eine Zelle übernimmt die Mehrheitsfarbe ihres 3×3-Felds (Kantensäume glätten).

    Nur wenn die Mehrheit mindestens ``need`` der neun Zellen hat und ihre Farbe
    höchstens ``max_delta_e`` entfernt ist. Eine Linie, eine Noppe breit, hat im 3×3-Feld
    höchstens drei eigene Zellen und bleibt daher stehen, solange ``need`` > 3 ist.
    """
    height, width = grid.shape
    padded = np.pad(grid, 1, constant_values=EMPTY_CELL)
    window = np.stack(
        [padded[dr : dr + height, dc : dc + width] for dr in range(3) for dc in range(3)]
    )
    colors = np.unique(grid[grid != EMPTY_CELL])
    if colors.size == 0:
        return grid.copy()
    # counts[k]: wie oft colors[k] im 3×3-Feld jeder Zelle vorkommt
    counts = np.stack([(window == color).sum(axis=0) for color in colors.tolist()])
    majority = colors[counts.argmax(axis=0)]
    own = np.where(grid == EMPTY_CELL, majority, grid)
    replace = (
        (grid != EMPTY_CELL)
        & (counts.max(axis=0) >= need)
        & (majority != grid)
        & (distances[own, majority] <= max_delta_e)
    )
    return np.where(replace, majority, grid).astype(np.intp)
