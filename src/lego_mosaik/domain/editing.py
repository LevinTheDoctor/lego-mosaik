"""Mosaik nachträglich bearbeiten: einzelne Noppen, Rechtecke oder Flächen umfärben.

Die Änderungen werden auf das geditherte Raster der Bildfläche angewendet, bevor
Rahmen, Relief, Formteile und Zerlegung entstehen. So passen Teile, Teileliste und
Anleitung danach wieder genau zum Bild. Reine Funktionen auf NumPy-Arrays, ohne I/O.

Koordinaten sind 1-basiert und zählen den Rahmen mit, genau wie die Zeilen- und
Spaltennummern in Vorschau und Bauanleitung.
"""

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

lazy import numpy as np

from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.models import BoolGrid, IndexGrid, LegoColor

_NEIGHBORS = ((-1, 0), (1, 0), (0, -1), (0, 1))


@dataclass(frozen=True, slots=True)
class StudEdit:
    """Eine Noppe umfärben."""

    row: int
    col: int
    color_id: int


@dataclass(frozen=True, slots=True)
class AreaEdit:
    """Ein Rechteck ab (``row``, ``col``) mit ``width`` × ``height`` Noppen umfärben."""

    row: int
    col: int
    width: int
    height: int
    color_id: int


@dataclass(frozen=True, slots=True)
class FillEdit:
    """Farbeimer: die zusammenhängende gleichfarbige Fläche um (``row``, ``col``) umfärben."""

    row: int
    col: int
    color_id: int


type GridEdit = StudEdit | AreaEdit | FillEdit


def connected(same: BoolGrid, seeds: Iterable[tuple[int, int]]) -> BoolGrid:
    """Alle Zellen mit ``same``, die über die vier Nachbarn mit einem Startpunkt verbunden sind.

    Flutfüllen als Breitensuche; Startpunkte ohne ``same`` werden ignoriert.
    """
    mask = np.zeros(same.shape, dtype=bool)
    queue = deque(cell for cell in seeds if same[cell])
    for cell in queue:
        mask[cell] = True
    height, width = same.shape
    while queue:
        row, col = queue.popleft()  # deque: Entnehmen vorne in O(1)
        for d_row, d_col in _NEIGHBORS:
            nr, nc = row + d_row, col + d_col
            if 0 <= nr < height and 0 <= nc < width and same[nr, nc] and not mask[nr, nc]:
                mask[nr, nc] = True
                queue.append((nr, nc))
    return mask


def apply_edits(
    grid: IndexGrid,
    palette: tuple[LegoColor, ...],
    edits: Sequence[GridEdit],
    allowed: Mapping[int, LegoColor],
    *,
    frame_width: int,
) -> tuple[IndexGrid, tuple[LegoColor, ...], BoolGrid]:
    """Wendet die Änderungen nacheinander auf das Raster der Bildfläche an.

    Args:
        grid: H×W-Raster der Bildfläche (ohne Rahmen) mit Indizes in ``palette``.
        palette: Die Farben des Rasters.
        edits: Änderungen in der Reihenfolge, in der sie gemacht wurden.
        allowed: Farben, die eingesetzt werden dürfen (Farb-ID → Farbe).
        frame_width: Rahmenbreite; Koordinaten zählen den Rahmen mit.

    Returns:
        Neues Raster, ggf. um neue Farben erweiterte Palette und eine Maske der
        geänderten Zellen (dort ist das Bild für die Formwahl einfarbig).

    Raises:
        ConfigError: Bei Koordinaten außerhalb der Bildfläche oder unerlaubter Farbe.
    """
    result = grid.copy()
    colors = list(palette)
    changed = np.zeros(grid.shape, dtype=bool)
    for edit in edits:
        color = allowed.get(edit.color_id)
        if color is None:
            raise ConfigError(
                f"Die Farbe {edit.color_id} gibt es nicht als 1×1-Teil der gewählten Teilarten."
            )
        if color not in colors:
            colors.append(color)
        cells = _cells(edit, result, frame_width)
        result[cells] = colors.index(color)
        changed |= cells
    return result, tuple(colors), changed


def _cells(edit: GridEdit, grid: IndexGrid, frame_width: int) -> BoolGrid:
    """Maske der Zellen, die eine Änderung betrifft."""
    row, col = _position(edit.row, edit.col, grid.shape, frame_width)
    cells = np.zeros(grid.shape, dtype=bool)
    match edit:
        case StudEdit():
            cells[row, col] = True
        case AreaEdit(width=width, height=height):
            if width < 1 or height < 1:
                raise ConfigError("Eine Fläche muss mindestens 1 × 1 Noppen groß sein.")
            _position(edit.row + height - 1, edit.col + width - 1, grid.shape, frame_width)
            cells[row : row + height, col : col + width] = True
        case FillEdit():
            cells = connected(grid == grid[row, col], [(row, col)])
    return cells


def _position(row: int, col: int, shape: tuple[int, ...], frame_width: int) -> tuple[int, int]:
    """1-basierte Mosaik-Koordinaten → 0-basierter Index in der Bildfläche."""
    height, width = shape
    inner_row, inner_col = row - 1 - frame_width, col - 1 - frame_width
    if not (0 <= inner_row < height and 0 <= inner_col < width):
        first, last_row, last_col = 1 + frame_width, height + frame_width, width + frame_width
        raise ConfigError(
            f"Zeile {row}, Spalte {col} liegt nicht in der Bildfläche "
            f"(Zeilen {first}–{last_row}, Spalten {first}–{last_col}; der Rahmen ist fest)."
        )
    return inner_row, inner_col


# --- JSON (eingaben.json und API) ------------------------------------------------------


def edit_to_json(edit: GridEdit) -> dict[str, object]:
    """Änderung als dict mit deutschen Schlüsseln (``art`` sagt, welche)."""
    data: dict[str, object] = {"zeile": edit.row, "spalte": edit.col, "farbe_id": edit.color_id}
    match edit:
        case StudEdit():
            return {"art": "noppe"} | data
        case AreaEdit():
            return {"art": "flaeche"} | data | {"breite": edit.width, "hoehe": edit.height}
        case FillEdit():
            return {"art": "fuellen"} | data


def edit_from_json(data: Mapping[str, object]) -> GridEdit:
    """Gegenstück zu ``edit_to_json``.

    Raises:
        ConfigError: Bei unbekannter Art oder fehlenden bzw. falschen Werten.
    """
    kind = data.get("art")
    try:
        values = {
            "row": _int(data["zeile"]),
            "col": _int(data["spalte"]),
            "color_id": _int(data["farbe_id"]),
        }
        if kind == "noppe":
            return StudEdit(**values)
        if kind == "fuellen":
            return FillEdit(**values)
        if kind == "flaeche":
            return AreaEdit(width=_int(data["breite"]), height=_int(data["hoehe"]), **values)
    except (KeyError, TypeError) as error:
        raise ConfigError(f"Ungültige Änderung {dict(data)}: {error}.") from None
    raise ConfigError(
        f"Unbekannte Art der Änderung „{kind}“. Gültig sind: noppe, flaeche, fuellen."
    )


def _int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"Ganzzahl erwartet, nicht {value!r}")
    return value
