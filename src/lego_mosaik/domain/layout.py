"""Layout: Mosaikgröße, Unterbau (Grundplatten oder Art-Paneele), Verbinder und Rahmen.

Reine Funktionen auf Domain-Objekten, ohne Abhängigkeit zu Konfiguration oder I/O.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

lazy import numpy as np
lazy import numpy.typing as npt

from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.models import (
    IndexGrid,
    LegoColor,
    PartCatalog,
    PartKind,
    PartType,
    Placement,
)

# Grenzen für die Mosaikgröße in Noppen
MIN_STUDS = 8  # Gesamtgröße pro Seite
MAX_STUDS = 384  # z. B. Weltkarte aus 48er-Platten: 8 × 5 Platten = 384 × 240
MIN_IMAGE_STUDS = 4  # Bildfläche innerhalb des Rahmens pro Seite

# Deutsche Bezeichnungen (mit passendem Artikel) für Fehlermeldungen
_NO_SUBSTRATE_LABELS = {
    PartKind.BASEPLATE: "keine Grundplatte",
    PartKind.PANEL: "kein LEGO-Art-Paneel",
    PartKind.PLATE: "keine Plate",
}
# Längste Plate in der Verbindungslage über großen Platten (Plate 16 × 16)
BOND_PART_STUDS = 16


@dataclass(frozen=True, slots=True)
class LayoutSize:
    """Abmessungen in Noppen.

    Attributes:
        width: Gesamtbreite des Mosaiks.
        height: Gesamthöhe des Mosaiks.
        image_width: Breite der Bildfläche innerhalb des Rahmens.
        image_height: Höhe der Bildfläche innerhalb des Rahmens.
        plates_wide: Anzahl Unterbau-Platten nebeneinander (deckt die Breite ab).
        plates_high: Anzahl Unterbau-Platten untereinander.
    """

    width: int
    height: int
    image_width: int
    image_height: int
    plates_wide: int
    plates_high: int


@dataclass(frozen=True, slots=True)
class ArtFormat:
    """Ein Format als Raster aus Unterbau-Platten (immer lückenlos baubar).

    Attributes:
        name: Kurzname für CLI, API und Menü, z. B. „quadrat“.
        plates_wide: Platten nebeneinander.
        plates_high: Platten untereinander.
        description: Deutsche Beschreibung.
    """

    name: str
    plates_wide: int
    plates_high: int
    description: str

    @property
    def aspect_ratio(self) -> float:
        """Breite / Höhe."""
        return self.plates_wide / self.plates_high


# Formate in Platten; mit LEGO-Art-Paneelen (16 Noppen) ergibt „quadrat“ 48 × 48 Noppen
FORMATS: tuple[ArtFormat, ...] = (
    ArtFormat("mini", 1, 1, "ein Paneel, z. B. Untersetzer oder Testbild"),
    ArtFormat("klein", 2, 2, "kleines Quadrat"),
    ArtFormat("quadrat", 3, 3, "wie die meisten LEGO-Art-Sets"),
    ArtFormat("gross", 4, 4, "großes Quadrat"),
    ArtFormat("riesig", 5, 5, "sehr großes Quadrat"),
    ArtFormat("quer", 4, 3, "Querformat 4:3"),
    ArtFormat("hoch", 3, 4, "Hochformat 3:4"),
    ArtFormat("breit", 6, 4, "Querformat 3:2, wie ein Foto"),
    ArtFormat("panorama", 6, 3, "Panorama 2:1"),
    ArtFormat("weltkarte", 8, 5, "wie LEGO Art Weltkarte (31203), 40 Paneele"),
)


def find_format(name: str) -> ArtFormat:
    """Format zum Namen.

    Raises:
        ConfigError: Bei unbekanntem Namen, mit allen gültigen Namen.
    """
    for art_format in FORMATS:
        if art_format.name == name.strip().lower():
            return art_format
    valid = ", ".join(art_format.name for art_format in FORMATS)
    raise ConfigError(f"Unbekanntes Format „{name}“. Gültig sind: {valid}.")


def suggest_formats(aspect_ratio: float, count: int = 3) -> tuple[ArtFormat, ...]:
    """Die Formate, deren Seitenverhältnis dem Bild am nächsten kommt (wenig Zuschnitt).

    Verglichen wird logarithmisch: 2:1 und 1:2 sind gleich weit von 1:1 entfernt.
    """
    ranked = sorted(
        FORMATS,
        key=lambda f: (abs(math.log(f.aspect_ratio / aspect_ratio)), f.plates_wide * f.plates_high),
    )
    return tuple(ranked[:count])


def snap_to_plates(studs: int, plate_studs: int) -> int:
    """Rundet auf ganze Platten (mindestens eine), damit der Unterbau genau passt.

    Beispiel: 40 Noppen auf 16er-Paneelen → 48 (drei Paneele), 23 → 16.
    """
    plates = max(1, math.floor(studs / plate_studs + 0.5))  # kaufmännisch runden
    return plates * plate_studs


def grid_layout_size(
    plate_studs: int, plates_wide: int, plates_high: int, frame_width: int
) -> LayoutSize:
    """Größe aus Platten: Gesamtgröße = Platten × Plattengröße, Bild = Gesamt − Rahmen."""
    width, height = plate_studs * plates_wide, plate_studs * plates_high
    return _size(width, height, frame_width, plates_wide, plates_high)


def custom_layout_size(width: int, height: int, plate_studs: int, frame_width: int) -> LayoutSize:
    """Freie Größe in Noppen; der Unterbau braucht dafür aufgerundet viele Platten.

    Mit Unterbau rastet die Pipeline die Größe vorher auf ganze Platten ein
    (``snap_to_plates``); ohne Unterbau ist jede Größe baubar und die Platten zählen
    hier nur für die Abschnitte.
    """
    # -(-a // b) ist die aufgerundete Ganzzahl-Division (// rundet sonst ab)
    plates_wide, plates_high = -(-width // plate_studs), -(-height // plate_studs)
    return _size(width, height, frame_width, plates_wide, plates_high)


def _size(width: int, height: int, frame: int, plates_wide: int, plates_high: int) -> LayoutSize:
    check_size(width, height, frame)
    return LayoutSize(
        width, height, width - 2 * frame, height - 2 * frame, plates_wide, plates_high
    )


def complete_size(
    width: int | None,
    height: int | None,
    aspect_ratio: float,
    frame_width: int,
    plate_studs: int | None = None,
) -> tuple[int, int]:
    """Ergänzt eine fehlende Seite so, dass die Bildfläche das Seitenverhältnis des Bilds hat.

    Args:
        width: Gewünschte Gesamtbreite oder ``None``.
        height: Gewünschte Gesamthöhe oder ``None``.
        aspect_ratio: Breite / Höhe des Bilds (z. B. 1.5 bei 3:2 quer).
        frame_width: Rahmenbreite in Noppen (gehört nicht zur Bildfläche).
        plate_studs: Mit Unterbau die Plattengröße: Beide Seiten rasten dann auf ganze
            Platten ein, die gegebene zuerst, die berechnete danach.

    Raises:
        ConfigError: Wenn beide Seiten fehlen.
    """

    def snap(studs: int) -> int:
        return studs if plate_studs is None else snap_to_plates(studs, plate_studs)

    frame = 2 * frame_width
    if width is not None and height is not None:
        return snap(width), snap(height)
    if width is not None:
        width = snap(width)
        return width, snap(round((width - frame) / aspect_ratio) + frame)
    if height is not None:
        height = snap(height)
        return snap(round((height - frame) * aspect_ratio) + frame), height
    raise ConfigError("Für eine freie Größe bitte Breite, Höhe oder beides angeben.")


def check_size(width: int, height: int, frame_width: int) -> None:
    """Prüft die Gesamtgröße und dass innerhalb des Rahmens genug Bildfläche bleibt.

    Raises:
        ConfigError: Mit verständlicher deutscher Meldung.
    """
    for label, value in (("Breite", width), ("Höhe", height)):
        if not MIN_STUDS <= value <= MAX_STUDS:
            raise ConfigError(
                f"Das Mosaik muss mindestens {MIN_STUDS} und darf höchstens {MAX_STUDS} "
                f"Noppen pro Seite haben ({label}: {value} Noppen)."
            )
    if min(width, height) - 2 * frame_width < MIN_IMAGE_STUDS:
        raise ConfigError(
            f"Der Rahmen ist zu breit: Innen müssen mindestens {MIN_IMAGE_STUDS} Noppen "
            "für das Bild bleiben."
        )


# --- Unterbau ------------------------------------------------------------------------


def find_substrate_part(
    catalog: PartCatalog, kinds: PartKind | Sequence[PartKind], studs: int
) -> PartType:
    """Sucht die quadratische Unterbau-Platte mit ``studs`` Noppen.

    Bei mehreren Teilarten gewinnt die erste, in der es die Größe gibt (z. B. eine echte
    Plate 16 × 16 vor der dünnen Grundplatte gleicher Größe).

    Raises:
        ConfigError: Wenn es keine solche Platte gibt; nennt die verfügbaren Größen.
    """
    kinds = (kinds,) if isinstance(kinds, PartKind) else tuple(kinds)
    for kind in kinds:
        for part in sorted(catalog.part_types.values(), key=lambda p: p.part_num):
            square = part.width_studs == part.length_studs == studs
            if part.kind is kind and square and not part.is_shaped:
                return part
    label = _NO_SUBSTRATE_LABELS.get(kinds[0], f"kein Teil der Art {kinds[0].value}")
    sizes = sorted(
        {
            p.length_studs
            for p in catalog.part_types.values()
            if p.kind in kinds and p.width_studs == p.length_studs >= MIN_STUDS
        }
    )
    available = ", ".join(map(str, sizes)) or "keine"
    raise ConfigError(f"Es gibt {label} mit {studs} × {studs} Noppen. Verfügbar: {available}.")


def find_connector(catalog: PartCatalog) -> PartType:
    """Der Verbinder (Technic-Pin) aus der Geometrie-Tabelle.

    Raises:
        ConfigError: Wenn kein Teil der Art ``connector`` eingetragen ist.
    """
    for part in sorted(catalog.part_types.values(), key=lambda p: p.part_num):
        if part.kind is PartKind.CONNECTOR:
            return part
    raise ConfigError("In teil_geometrie.csv steht kein Verbinder (Teilart „connector“).")


def require_part_color(catalog: PartCatalog, part: PartType, color_id: int) -> LegoColor:
    """Prüft, dass es das Teil in der Farbe gibt, und liefert die Farbe.

    Raises:
        ConfigError: Sonst, mit den verfügbaren Farben dieses Teils.
    """
    color = catalog.colors.get(color_id)
    if (
        color is not None
        and color.ldraw_code is not None
        and catalog.is_available(part.part_num, color_id)
    ):
        return color
    available = sorted(
        f"{c.id} {c.name}"
        for c in catalog.colors.values()
        if catalog.is_available(part.part_num, c.id) and c.ldraw_code is not None
    )
    raise ConfigError(
        f"Das Teil {part.part_num} ({part.name}) gibt es nicht in Farbe {color_id}. "
        f"Verfügbar: {', '.join(available) or 'keine'}."
    )


def require_single_stud_color(
    catalog: PartCatalog, color_id: int, part_kinds: Sequence[PartKind], label: str
) -> LegoColor:
    """Prüft, dass sich eine Farbe Noppe für Noppe legen lässt (1×1-Teil vorhanden).

    Args:
        catalog: Der Teilekatalog.
        color_id: Rebrickable-Farb-ID.
        part_kinds: Erlaubte Teilarten.
        label: Bezeichnung für die Meldung, z. B. „Rahmenfarbe“.

    Raises:
        ConfigError: Wenn die Farbe unbekannt ist, keinen LDraw-Code hat oder kein
            1×1-Teil der erlaubten Teilarten in ihr existiert.
    """
    color = catalog.colors.get(color_id)
    if color is None or color.ldraw_code is None:
        raise ConfigError(f"Die {label} {color_id} ist unbekannt.")
    if not any(part.is_single_stud for part in catalog.parts_in_color(color_id, part_kinds)):
        kinds = ", ".join(kind.value for kind in part_kinds)
        raise ConfigError(f"Für die {label} {color.name} gibt es kein 1×1-Teil ({kinds}).")
    return color


def substrate_placements(
    part: PartType, color: LegoColor, plates_wide: int, plates_high: int
) -> tuple[Placement, ...]:
    """Unterbau-Platten lückenlos nebeneinander, zeilenweise von oben links."""
    studs = part.length_studs
    return tuple(
        Placement(part, color, row=plate_row * studs, col=plate_col * studs)
        for plate_row in range(plates_high)
        for plate_col in range(plates_wide)
    )


def pin_offsets(plate_studs: int, pins_per_seam: int) -> tuple[int, ...]:
    """Positionen der Pins entlang einer Naht, gleichmäßig verteilt.

    Jeder Pin sitzt in der Mitte seines Anteils: Bei 16 Noppen und 2 Pins sind das
    die Noppen 4 und 12 (Mitte der ersten bzw. zweiten Hälfte).
    """
    # (2k + 1) / (2n) ist die Mitte des k-ten von n gleich großen Stücken
    return tuple((2 * k + 1) * plate_studs // (2 * pins_per_seam) for k in range(pins_per_seam))


def connector_placements(
    pin: PartType,
    color: LegoColor,
    size: LayoutSize,
    plate_studs: int,
    pins_per_seam: int,
) -> tuple[Placement, ...]:
    """Technic-Pins an allen Nähten zwischen benachbarten Paneelen.

    Ein Pin ist 2 Noppen lang und steckt zur Hälfte in jedem der beiden Paneele.
    Senkrechte Nähte (zwischen Spalten) bekommen ungedrehte Pins, waagerechte Nähte
    (zwischen Zeilen) gedrehte.
    """
    offsets = pin_offsets(plate_studs, pins_per_seam)
    pins: list[Placement] = []
    for plate_row in range(size.plates_high):
        for plate_col in range(size.plates_wide):
            top, left = plate_row * plate_studs, plate_col * plate_studs
            if plate_col + 1 < size.plates_wide:  # Nachbar rechts → senkrechte Naht
                seam = left + plate_studs
                pins += [Placement(pin, color, top + o, seam - 1) for o in offsets]
            if plate_row + 1 < size.plates_high:  # Nachbar unten → waagerechte Naht
                seam = top + plate_studs
                pins += [Placement(pin, color, seam - 1, left + o, rotated=True) for o in offsets]
    return tuple(pins)


def running_bond_offset(seam_spacing: int, part_length: int) -> int:
    """Versatz für eine Lage „auf Lücke“ (Mauerverband) über den Nähten des Unterbaus.

    Die Lage wird in Abschnitte der Größe ``seam_spacing`` geteilt, die um den
    Versatz gegenüber den Nähten verschoben sind. Gleich lange Teile liegen dann ab
    Abschnittsbeginn hintereinander; eine Naht wird überbrückt, wenn ihr Abstand zum
    Abschnittsbeginn kein Vielfaches der Teillänge ist.

    Bevorzugt wird die halbe Teillänge (wie bei Ziegeln), sonst 1 oder 2 Noppen.

    Args:
        seam_spacing: Abstand der Nähte (Plattengröße des Unterbaus) in Noppen.
        part_length: Längstes verfügbares Teil der Lage in Noppen.
    """
    if part_length < 2:
        return 0  # 1×1-Teile können keine Naht überbrücken
    for offset in (part_length // 2, 1, 2):
        # % ergibt den Rest; ist er nicht 0, liegt die Naht mitten in einem Teil
        if (seam_spacing - offset) % part_length:
            return offset
    return 0


# --- Rahmen --------------------------------------------------------------------------


def frame_mask(height: int, width: int, frame_width: int) -> npt.NDArray[np.bool_]:
    """H×W-Maske: True auf dem äußeren Ring der Breite ``frame_width``."""
    mask = np.zeros((height, width), dtype=bool)
    if frame_width > 0:
        mask[:frame_width, :] = mask[-frame_width:, :] = True  # oben und unten
        mask[:, :frame_width] = mask[:, -frame_width:] = True  # links und rechts
    return mask


def corner_mask(height: int, width: int, frame_width: int) -> npt.NDArray[np.bool_]:
    """H×W-Maske: True in den vier Eckquadraten (``frame_width`` × ``frame_width``) des Rahmens."""
    mask = np.zeros((height, width), dtype=bool)
    if frame_width > 0:
        # Slices mit negativem Start zählen vom Ende: -f: sind die letzten f Zeilen/Spalten
        for rows in (slice(None, frame_width), slice(-frame_width, None)):
            for cols in (slice(None, frame_width), slice(-frame_width, None)):
                mask[rows, cols] = True
    return mask


def apply_frame(
    color_grid: IndexGrid,
    palette: Sequence[LegoColor],
    frame_width: int,
    frame_color: LegoColor,
    corner_color: LegoColor | None = None,
) -> tuple[IndexGrid, tuple[LegoColor, ...]]:
    """Legt einen Rahmen um das Farbraster; die Ecken optional in eigener Farbe.

    Fehlende Rahmenfarben werden hinten an die Palette angehängt; die Indizes der
    übrigen Farben bleiben dadurch unverändert.

    Returns:
        Das größere Raster und die (ggf. erweiterte) Palette.
    """
    if frame_width == 0:
        return color_grid, tuple(palette)
    colors = list(palette)
    frame_index = _index_of(colors, frame_color)
    # np.pad hängt auf allen vier Seiten frame_width Zellen mit dem Rahmenindex an
    framed = np.pad(color_grid, frame_width, mode="constant", constant_values=frame_index)
    if corner_color is not None:
        height, width = framed.shape
        # Boolesche Maske als Index: setzt genau die Zellen, an denen die Maske True ist
        framed[corner_mask(height, width, frame_width)] = _index_of(colors, corner_color)
    return framed.astype(np.intp), tuple(colors)


def _index_of(colors: list[LegoColor], color: LegoColor) -> int:
    """Index der Farbe in der Liste; fehlt sie, wird sie angehängt."""
    if color not in colors:
        colors.append(color)
    return colors.index(color)
