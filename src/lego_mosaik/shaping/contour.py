"""Stil „formen“: Formteile dort, wo sie eine Kontur genauer treffen als eine volle Noppe.

Idee: Das Bild liegt zusätzlich in feiner Auflösung vor (``subsample`` × ``subsample``
Teilpixel pro Noppe). Für jede Stelle wird verglichen, wie gut sie aussieht als

- volle Noppen in der bestpassenden Farbe oder
- Formteil (Rundungen, Dreieck, Cut Corner, Wedge Plates) in jeder Drehung: Wo das
  Teil liegt, zählt seine Farbe, in den Lücken die Farbe darunter.

Darunter liegt der Untergrund (Paneel bzw. Relief-Stützlage). Mit ``underlay`` darf ein
Formteil auch auf einer Plate in einer zweiten Farbe liegen, wie die Plate-Stapel bei
Brick Sketches: Dann zeigt die Lücke z. B. die Farbe des Nachbarbereichs, und das
Formteil steht eine Plate höher. Ohne Untergrund (Lab-Wert NaN) geht nur das.

Volle Noppe und Formteil dürfen ihre Farben frei wählen. So gewinnt ein Formteil nur,
wenn seine *Form* die Kontur besser trifft, nicht bloß, weil eine andere Farbe besser
passt als die geditherete. Wo kein Formteil gewinnt, bleibt das Dithering unverändert.

Erst werden 1 × 1-Teile gewählt, dann die größeren (größter Gewinn zuerst); ein großes
Teil verdrängt die 1 × 1-Wahl seiner Noppen, wenn es insgesamt besser ist.

Der Fehler ist die Summe der quadrierten Lab-Abstände (CIE76) aller Teilpixel. Für eine
feste Form lässt sich die beste Farbe ohne Schleife über die Pixel bestimmen:

    Σ M·|B − f|² = Σ M·|B|² − 2·f·Σ M·B + |f|²·Σ M

(M = Abdeckung, B = Bildpixel, f = Teilfarbe). Die drei Summen („Momente“) hängen nicht
von f ab; so wird jede Farbe der Palette für alle Stellen auf einmal bewertet.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

lazy import numpy as np
lazy import numpy.typing as npt

from lego_mosaik.domain.models import (
    EMPTY_CELL,
    PLATE_HEIGHT_LDU,
    FloatImage,
    IndexGrid,
    LegoColor,
    PartCatalog,
    PartKind,
    PartShape,
    PartType,
    Placement,
)
from lego_mosaik.domain.shapes import base_outline, box, coverage, part_turns
from lego_mosaik.shaping.protocol import ShapeResult

# Bei mehreren Teilen mit gleichem Umriss: glatte Tiles vor Plates (wie das übrige Bild)
KIND_PREFERENCE = (PartKind.TILE, PartKind.PLATE)
# Eine Unterlage (zusätzliche Plate) lohnt sich erst, wenn sie den Lückenfehler deutlich senkt
UNDERLAY_GAIN = 0.3
NO_UNDERLAY = -1  # Farbindex „keine Unterlage, in der Lücke ist der Untergrund“

type FloatArray = npt.NDArray[np.float64]
type BoolArray = npt.NDArray[np.bool_]
type Moments = tuple[float, FloatArray, FloatArray]


@dataclass(frozen=True, slots=True)
class _Option:
    """Ein Formteil in einer Drehung, bereit zum Bewerten."""

    part: PartType
    turns: int
    mask: FloatArray  # Abdeckung pro Teilpixel (Zeilen·s × Spalten·s)
    available: BoolArray  # pro Palettenfarbe: gibt es das Teil darin?

    @property
    def rows(self) -> int:
        return box(self.part, self.turns)[0]

    @property
    def cols(self) -> int:
        return box(self.part, self.turns)[1]

    @property
    def cells(self) -> int:
        return self.rows * self.cols


@dataclass(frozen=True, slots=True)
class _Choice:
    """Bewertung einer Option für alle Fenster: Fehler, Teilfarbe, Unterlagenfarbe."""

    cost: FloatArray
    color: npt.NDArray[np.intp]
    underlay: npt.NDArray[np.intp]  # NO_UNDERLAY oder Farbindex der Plate darunter


@dataclass(frozen=True, slots=True)
class _Picked:
    """Ein gewähltes Formteil (Position relativ zum Raster, Farben als Palettenindex)."""

    row: int
    col: int
    option: _Option
    color: int
    underlay: int

    @property
    def area(self) -> tuple[slice, slice]:
        return (
            slice(self.row, self.row + self.option.rows),
            slice(self.col, self.col + self.option.cols),
        )


@dataclass(frozen=True, slots=True)
class _CellSums:
    """Vorberechnete Größen pro Noppe und Teilpixel (H × W × s × s)."""

    blocks: FloatArray  # Bildpixel (H, W, s, s, 3)
    norm: FloatArray  # |B|²
    ground: FloatArray  # |B − Untergrund|², NaN ohne Untergrund


class ContourShaper:
    """Wählt Formteile an Kanten (Strategy für den Stil „formen“).

    Args:
        shapes: Erlaubte Arten von Formteilen.
        subsample: Teilpixel pro Noppe und Richtung.
        min_gain: Ein Formteil muss den Fehler um mindestens diesen Anteil senken.
        min_improvement: … und im Mittel um mindestens so viel ΔE pro Teilpixel. Das
            verhindert Formteile in ruhigen Flächen, die nur Bildrauschen nachzeichnen.
        underlay: Formteile dürfen auf einer Plate in eigener Farbe liegen (eine Plate höher).
    """

    def __init__(
        self,
        shapes: Sequence[PartShape],
        *,
        subsample: int,
        min_gain: float,
        min_improvement: float,
        underlay: bool,
    ) -> None:
        self._shapes = tuple(shapes)
        self.subsample = subsample
        self._min_gain = min_gain
        self._min_improvement = min_improvement
        self._underlay = underlay

    def accepts(self, color: LegoColor, catalog: PartCatalog) -> bool:
        """Jede Farbe taugt: Wo es kein Formteil in ihr gibt, bleibt die Noppe eckig."""
        return True

    def refine(
        self,
        grid: IndexGrid,
        detail_lab: FloatImage | None,
        palette: Sequence[LegoColor],
        palette_lab: FloatImage,
        catalog: PartCatalog,
        *,
        levels: IndexGrid,
        background_lab: FloatImage,
    ) -> ShapeResult:
        """Formteile (und ihre Unterlagen) für ein Raster, in der Pipeline ein Bauabschnitt."""
        if detail_lab is None:
            raise ValueError("Der Stil „formen“ braucht ein Detailbild (subsample > 0).")
        height, width = grid.shape
        s = self.subsample
        used = grid != EMPTY_CELL
        # (H·s, W·s, 3) → (H, W, s, s, 3): je Noppe ein s × s-Block aus Teilpixeln
        blocks = detail_lab.reshape(height, s, width, s, 3).transpose(0, 2, 1, 3, 4)
        sums = _CellSums(
            blocks=blocks,
            norm=(blocks**2).sum(axis=-1),
            ground=((blocks - background_lab[:, :, None, None, :]) ** 2).sum(axis=-1),
        )
        safe = np.where(used, grid, 0)  # EMPTY_CELL wäre als Index ungültig
        dithered = ((blocks - palette_lab[safe][:, :, None, None, :]) ** 2).sum(axis=(2, 3, 4))
        everything = np.ones(len(palette), dtype=bool)
        full = _moments(np.ones((s, s)), sums, 1, 1)
        reference, _ = _best_color(full, everything, palette_lab)
        plates = np.array([_plate(catalog, color, 1, 1) is not None for color in palette])
        options = self._options(palette, catalog)
        singles, current = self._single_studs(
            [o for o in options if o.cells == 1], sums, palette_lab, plates, reference, dithered
        )
        larger = self._larger(
            [o for o in options if o.cells > 1],
            sums,
            palette_lab,
            plates,
            _Grids(reference, current, used, levels),
        )
        taken = np.zeros(grid.shape, dtype=bool)
        for pick in larger:
            taken[pick.area] = True
        # 1 × 1-Formteile nur auf belegten Zellen, auf denen kein größeres Teil liegt
        chosen = larger + [p for p in singles if used[p.row, p.col] and not taken[p.row, p.col]]
        return _result(chosen, palette, catalog)

    # --- Kandidaten -----------------------------------------------------------------

    def _options(self, palette: Sequence[LegoColor], catalog: PartCatalog) -> list[_Option]:
        """Alle erlaubten Formteile in allen sinnvollen Drehungen.

        Gibt es denselben Umriss als Tile und als Plate (z. B. rund), zählt nur das Tile.
        """
        options = []
        for part in _distinct_parts(catalog, self._shapes):
            available = np.array([catalog.is_available(part.part_num, c.id) for c in palette])
            if not available.any():
                continue
            for turns in part_turns(part):
                options.append(
                    _Option(part, turns, coverage(part, turns, self.subsample), available)
                )
        return options

    def _evaluate(
        self, option: _Option, sums: _CellSums, palette_lab: FloatImage, plates: BoolArray
    ) -> _Choice:
        """Fehler eines Formteils für alle Fenster: Teilfarbe plus Lücke (Untergrund/Unterlage)."""
        rows, cols, mask = option.rows, option.cols, option.mask
        cost, color = _best_color(_moments(mask, sums, rows, cols), option.available, palette_lab)
        gaps = _weighted(sums.ground, 1 - mask, rows, cols)
        gaps = np.where(np.isnan(gaps), np.inf, gaps)  # ohne Untergrund: Lücke nur mit Unterlage
        underlay = np.full(cost.shape, NO_UNDERLAY)
        if self._underlay:
            own, own_color = _best_color(_moments(1 - mask, sums, rows, cols), plates, palette_lab)
            better = own < gaps * (1 - UNDERLAY_GAIN)
            gaps = np.where(better, own, gaps)
            underlay = np.where(better, own_color, NO_UNDERLAY)
        return _Choice(cost + gaps, color, underlay)

    # --- 1 × 1 ----------------------------------------------------------------------

    def _single_studs(
        self,
        options: Sequence[_Option],
        sums: _CellSums,
        palette_lab: FloatImage,
        plates: BoolArray,
        reference: FloatArray,
        dithered: FloatArray,
    ) -> tuple[list[_Picked], FloatArray]:
        """Bestes 1 × 1-Formteil je Noppe, falls es deutlich besser ist als jede volle Noppe.

        Returns:
            Die gewählten Formteile und der Fehler jeder Noppe mit der getroffenen Wahl
            (gedithert, wo keins gewählt wurde), für den Vergleich mit größeren Teilen.
        """
        choices = [self._evaluate(option, sums, palette_lab, plates) for option in options]
        best_cost = reference.copy()
        best = np.full(reference.shape, -1)
        for index, choice in enumerate(choices):
            better = choice.cost < best_cost
            best_cost[better], best[better] = choice.cost[better], index
        accept = (best >= 0) & self._worthwhile(reference, best_cost, cells=1)
        picks = []
        for row, col in zip(*np.nonzero(accept), strict=True):
            choice = choices[best[row, col]]
            picks.append(
                _Picked(
                    int(row),
                    int(col),
                    options[best[row, col]],
                    int(choice.color[row, col]),
                    int(choice.underlay[row, col]),
                )
            )
        return picks, np.where(accept, best_cost, dithered)

    # --- größere Teile --------------------------------------------------------------

    def _larger(
        self,
        options: Sequence[_Option],
        sums: _CellSums,
        palette_lab: FloatImage,
        plates: BoolArray,
        grids: _Grids,
    ) -> list[_Picked]:
        """Formteile über mehrere Noppen, größter Gewinn zuerst, ohne Überlappung."""
        height, width = grids.reference.shape
        candidates = []
        for option in options:
            rows, cols = option.rows, option.cols
            if rows > height or cols > width:
                continue
            valid = grids.valid_windows(rows, cols)
            reference = _window(grids.reference, rows, cols, np.add)
            current = _window(grids.current, rows, cols, np.add)
            choice = self._evaluate(option, sums, palette_lab, plates)
            worthwhile = self._worthwhile(reference, choice.cost, option.cells)
            accept = valid & (choice.cost < current) & worthwhile
            for top, left in zip(*np.nonzero(accept), strict=True):
                gain = float(current[top, left] - choice.cost[top, left])
                color, under = int(choice.color[top, left]), int(choice.underlay[top, left])
                candidates.append((gain, _Picked(int(top), int(left), option, color, under)))
        # Größter Gewinn zuerst; Position und Teil machen die Reihenfolge eindeutig
        candidates.sort(
            key=lambda c: (-c[0], c[1].row, c[1].col, c[1].option.part.part_num, c[1].option.turns)
        )
        taken = np.zeros(grids.reference.shape, dtype=bool)
        chosen = []
        for _, pick in candidates:
            if not taken[pick.area].any():
                taken[pick.area] = True
                chosen.append(pick)
        return chosen

    def _worthwhile(self, reference: FloatArray, cost: FloatArray, cells: int) -> BoolArray:
        """Lohnt sich das Formteil gegenüber der besten vollen Noppe?

        Relativ (``min_gain``) und absolut (``min_improvement``) deutlich besser.
        """
        subpixels = cells * self.subsample**2
        relative = cost <= reference * (1 - self._min_gain)
        # Mittlere Verbesserung des quadrierten Fehlers je Teilpixel, verglichen mit ΔE²
        absolute = (reference - cost) / subpixels >= self._min_improvement**2
        return relative & absolute


@dataclass(frozen=True, slots=True)
class _Grids:
    """Raster pro Noppe, gegen die größere Teile antreten."""

    reference: FloatArray  # Fehler der besten vollen Noppe
    current: FloatArray  # Fehler der bisherigen Wahl (gedithert oder 1 × 1-Formteil)
    used: BoolArray
    levels: IndexGrid

    def valid_windows(self, rows: int, cols: int) -> BoolArray:
        """Fenster, deren Noppen alle belegt und gleich hoch sind (kein Teil über eine Stufe)."""
        all_used: BoolArray = _window(self.used.astype(np.float64), rows, cols, np.minimum) > 0
        levels = self.levels.astype(np.float64)
        low, high = _window(levels, rows, cols, np.minimum), _window(levels, rows, cols, np.maximum)
        flat: BoolArray = low == high
        return all_used & flat


def _distinct_parts(catalog: PartCatalog, shapes: Sequence[PartShape]) -> list[PartType]:
    """Alle Teile der erlaubten Formarten; bei gleichem Umriss das Tile vor der Plate."""
    chosen: dict[tuple[object, ...], PartType] = {}
    for shape in shapes:
        for kind in KIND_PREFERENCE:
            for part in catalog.parts_with_shape(shape):
                key = (shape, part.box, base_outline(part))
                if part.kind is kind and key not in chosen:
                    chosen[key] = part
    return list(chosen.values())


def _plate(catalog: PartCatalog, color: LegoColor, rows: int, cols: int) -> Placement | None:
    """Eckige Plate über ``rows`` × ``cols`` Noppen in der Farbe (an Position 0, 0) oder None."""
    short, long = sorted((rows, cols))
    for part in catalog.parts_in_color(color.id, (PartKind.PLATE,)):
        if (part.width_studs, part.length_studs) == (short, long):
            # ungedreht liegt die lange Seite entlang der Spalten
            return Placement(part, color, 0, 0, rotated=rows > cols)
    return None


def _result(
    picks: Sequence[_Picked], palette: Sequence[LegoColor], catalog: PartCatalog
) -> ShapeResult:
    """Platzierungen der Formteile; mit Unterlage liegt das Formteil eine Plate höher.

    Die Unterlage ist eine Plate über die ganze Box, wenn es sie in der Farbe gibt, sonst
    lauter 1 × 1-Plates.
    """
    parts: list[Placement] = []
    underlays: list[Placement] = []
    for pick in picks:
        option, raised = pick.option, pick.underlay != NO_UNDERLAY
        parts.append(
            Placement(
                option.part,
                palette[pick.color],
                pick.row,
                pick.col,
                turns=option.turns,
                bottom_ldu=PLATE_HEIGHT_LDU if raised else 0,
            )
        )
        if not raised:
            continue
        color = palette[pick.underlay]
        whole = _plate(catalog, color, option.rows, option.cols)
        if whole is not None:
            underlays.append(Placement(whole.part, color, pick.row, pick.col, whole.rotated))
            continue
        single = _plate(catalog, color, 1, 1)
        assert single is not None  # nur Farben mit 1 × 1-Plate kommen als Unterlage in Frage
        underlays += [
            Placement(single.part, color, pick.row + d_row, pick.col + d_col)
            for d_row in range(option.rows)
            for d_col in range(option.cols)
        ]
    return ShapeResult(tuple(parts), tuple(underlays))


def _pieces(weights: FloatArray, s: int, rows: int, cols: int) -> list[tuple[int, int, FloatArray]]:
    """Teilt eine Abdeckung über ``rows`` × ``cols`` Noppen in Stücke je Noppe."""
    return [
        (d_row, d_col, weights[d_row * s : (d_row + 1) * s, d_col * s : (d_col + 1) * s])
        for d_row in range(rows)
        for d_col in range(cols)
    ]


def _moments(weights: FloatArray, sums: _CellSums, rows: int, cols: int) -> Moments:
    """Σ M, Σ M·B und Σ M·|B|² für jedes Fenster aus ``rows`` × ``cols`` Noppen.

    Jedes Stück der Abdeckung wird für alle Noppen ausgewertet und um seine Lage im
    Fenster verschoben aufsummiert. Ergebnis: Arrays der Form (H − rows + 1, W − cols + 1).
    """
    s = sums.blocks.shape[2]
    height, width = sums.norm.shape[:2]
    out_rows, out_cols = height - rows + 1, width - cols + 1
    m0, m1, m2 = 0.0, np.zeros((out_rows, out_cols, 3)), np.zeros((out_rows, out_cols))
    for d_row, d_col, piece in _pieces(weights, s, rows, cols):
        window = (slice(d_row, d_row + out_rows), slice(d_col, d_col + out_cols))
        m0 += float(piece.sum())
        # einsum summiert über die Teilpixel (i, j), gewichtet mit der Abdeckung
        m1 = m1 + np.einsum("hwijc,ij->hwc", sums.blocks, piece)[window]
        m2 = m2 + np.einsum("hwij,ij->hw", sums.norm, piece)[window]
    return m0, m1, m2


def _weighted(values: FloatArray, weights: FloatArray, rows: int, cols: int) -> FloatArray:
    """Σ weights · values je Fenster (wie ``_moments``, für einen Fehler pro Teilpixel)."""
    s = values.shape[2]
    height, width = values.shape[:2]
    out_rows, out_cols = height - rows + 1, width - cols + 1
    total = np.zeros((out_rows, out_cols))
    for d_row, d_col, piece in _pieces(weights, s, rows, cols):
        window = (slice(d_row, d_row + out_rows), slice(d_col, d_col + out_cols))
        total = total + np.einsum("hwij,ij->hw", values, piece)[window]
    return total


def _best_color(
    moments: Moments, available: BoolArray, palette_lab: FloatImage
) -> tuple[FloatArray, npt.NDArray[np.intp]]:
    """Beste verfügbare Farbe und ihr Fehler nach Σ M·|B − f|² = m2 − 2·f·m1 + |f|²·m0."""
    m0, m1, m2 = moments
    # (H, W, 3) @ (3, P) → (H, W, P): Skalarprodukt mit jeder Palettenfarbe
    errors = m2[..., None] - 2 * (m1 @ palette_lab.T) + m0 * (palette_lab**2).sum(axis=1)
    errors[..., ~available] = np.inf  # Farben ohne dieses Teil kommen nicht in Frage
    color = errors.argmin(axis=-1)
    return np.take_along_axis(errors, color[..., None], axis=-1)[..., 0], color


def _window(
    values: FloatArray,
    rows: int,
    cols: int,
    combine: Callable[[FloatArray, FloatArray], FloatArray],
) -> FloatArray:
    """Fasst jedes Fenster aus ``rows`` × ``cols`` Zellen zusammen (Summe, Minimum, …)."""
    height, width = values.shape
    out_rows, out_cols = height - rows + 1, width - cols + 1
    result = values[:out_rows, :out_cols]
    for d_row in range(rows):
        for d_col in range(cols):
            if d_row or d_col:
                result = combine(result, values[d_row : d_row + out_rows, d_col : d_col + out_cols])
    return result
