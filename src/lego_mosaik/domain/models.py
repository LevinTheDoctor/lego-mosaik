"""Domain-Modelle: die Fachbegriffe des Mosaiks als unveränderliche Datenklassen.

Dieses Modul hängt von keinem anderen Teil des Pakets ab (Abhängigkeitsregel).
Alle Klassen sind ``frozen``: Einmal erzeugt, ändern sie sich nie. Das macht sie
sicher teilbar zwischen Threads (FastAPI-Threadpool) und leicht testbar.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

lazy import numpy as np
lazy import numpy.typing as npt

# Typ-Alias mit ``type`` (PEP 695): Der rechte Teil wird erst ausgewertet, wenn
# jemand den Alias wirklich auflöst (z. B. mypy). Zur Laufzeit wird NumPy dadurch
# nicht geladen, obwohl der Alias in vielen Annotationen steht.
type IndexGrid = npt.NDArray[np.intp]
"""H×W-Raster mit Palettenindizes (ganze Zahlen)."""

type FloatImage = npt.NDArray[np.float64]
"""H×W×3-Bild mit Gleitkommawerten, z. B. im Lab-Farbraum."""

type RgbTuple = tuple[int, int, int]
"""Farbe als (Rot, Grün, Blau), je 0–255."""

# Höhe einer Plate in LDraw-Einheiten (LDU). Eine Noppe ist 20 LDU breit.
PLATE_HEIGHT_LDU = 8

# Palettenindex für Zellen, die eine Zerlegung frei lassen soll (z. B. die
# Innenfläche, wenn nur der Rahmen zerlegt wird). Echte Indizes sind immer >= 0.
EMPTY_CELL = -1


class PartKind(StrEnum):
    """Teilart. ``StrEnum``: Jeder Wert ist zugleich ein ``str`` ("plate" == PartKind.PLATE)."""

    PLATE = "plate"
    TILE = "tile"
    BRICK = "brick"
    BASEPLATE = "baseplate"  # dünne Grundplatte, nur als Untergrund
    PANEL = "panel"  # LEGO-Art-Paneel 16 × 16 mit Technic-Löchern, nur als Untergrund
    CONNECTOR = "connector"  # Technic-Pin, verbindet Paneele an den Seiten


# Teilarten, aus denen das Mosaik selbst bestehen darf
MOSAIC_PART_KINDS = (PartKind.PLATE, PartKind.TILE, PartKind.BRICK)


class Substrate(StrEnum):
    """Worauf das Mosaik gebaut wird (der Unterbau)."""

    BASEPLATE = "baseplate"  # dünne Grundplatten; mehrere liegen nur lose nebeneinander
    ART_PANEL = "art_panel"  # LEGO-Art-Paneele, an den Seiten mit Technic-Pins verbunden
    NONE = "none"  # ohne Unterbau (z. B. auf eigene Platten)


# Welche Teilart den jeweiligen Unterbau bildet (NONE hat keine)
SUBSTRATE_PART_KINDS: Mapping[Substrate, PartKind] = {
    Substrate.BASEPLATE: PartKind.BASEPLATE,
    Substrate.ART_PANEL: PartKind.PANEL,
}


class LongAxis(StrEnum):
    """Achse, entlang der die lange Seite eines Teils in seiner LDraw-Datei liegt."""

    X = "x"
    Z = "z"


class ColorCategory(StrEnum):
    """Oberflächen-Kategorie einer Farbe (wie in Bricklink Studio gruppiert)."""

    SOLID = "solid"  # normale, deckende Farben
    TRANSPARENT = "transparent"
    METALLIC = "metallic"
    PEARL = "pearl"
    CHROME = "chrome"
    GLITTER = "glitter"
    SPECKLE = "speckle"
    SATIN = "satin"
    MILKY = "milky"  # z. B. Glow in Dark
    RUBBER = "rubber"  # Gummi, für Mosaike ungeeignet


class FitMode(StrEnum):
    """Wie das Bild auf das Seitenverhältnis des Mosaiks gebracht wird."""

    CROP = "crop"  # mittig zuschneiden
    PAD = "pad"  # vollständig einpassen und mit Randfarbe auffüllen


class ColorDistance(StrEnum):
    """Formel für den Farbabstand im Lab-Raum."""

    CIE76 = "cie76"  # euklidischer Abstand, schnell
    CIEDE2000 = "ciede2000"  # wahrnehmungsnäher, langsamer


class Resampling(StrEnum):
    """Verfahren zum Verkleinern des Bilds (Namen wie in ``PIL.Image.Resampling``)."""

    BOX = "box"  # mittelt Flächen, gut für starkes Verkleinern
    BILINEAR = "bilinear"
    LANCZOS = "lanczos"
    NEAREST = "nearest"


@dataclass(frozen=True, slots=True)
class LegoColor:
    """Eine echte LEGO-Farbe.

    Attributes:
        id: Rebrickable-Farb-ID.
        name: Englischer Farbname aus Rebrickable, z. B. "Bright Green".
        rgb: Farbwert als (R, G, B), je 0–255.
        is_transparent: True bei transparenten Farben.
        ldraw_code: Farbcode für LDraw-Dateien; ``None``, wenn keine sichere Zuordnung bekannt ist.
        category: Oberflächen-Kategorie (solid, metallic, pearl, …).
        bricklink_code: BrickLink-Farbcode, falls bekannt (für die Wanted List).
        lego_name: Offizieller LEGO-Farbname, falls bekannt (z. B. "Bright Red").
    """

    id: int
    name: str
    rgb: RgbTuple
    is_transparent: bool
    ldraw_code: int | None
    category: ColorCategory = ColorCategory.SOLID
    bricklink_code: int | None = None
    lego_name: str | None = None

    @property
    def hex(self) -> str:
        """Farbwert als Hex-String mit ``#``, z. B. ``#05131D``."""
        # :02X formatiert eine Zahl als zweistellige Hex-Zahl in Großbuchstaben (5 -> "05")
        red, green, blue = self.rgb
        return f"#{red:02X}{green:02X}{blue:02X}"


@dataclass(frozen=True, slots=True)
class PartType:
    """Ein Teil mit seiner Geometrie (aus ``daten/teil_geometrie.csv``).

    Attributes:
        part_num: Rebrickable-Teilenummer, z. B. "3024".
        name: Teilname aus Rebrickable, z. B. "Plate 1 x 1".
        kind: Teilart (Plate, Tile, Brick).
        width_studs: Kurze Seite in Noppen.
        length_studs: Lange Seite in Noppen (>= width_studs).
        ldraw_file: Dateiname in der LDraw-Bibliothek, z. B. "3024.dat".
        height_ldu: Höhe in LDU (Plate/Tile 8, Brick 24).
        ldraw_long_axis: Achse der langen Seite in der LDraw-Datei.
        bricklink_id: Teilenummer bei BrickLink, falls abweichend (z. B. 3070 statt 3070b).
    """

    part_num: str
    name: str
    kind: PartKind
    width_studs: int
    length_studs: int
    ldraw_file: str
    height_ldu: int
    ldraw_long_axis: LongAxis = LongAxis.X
    bricklink_id: str | None = None

    @property
    def bricklink_item(self) -> str:
        """Teilenummer für BrickLink (fällt auf die Rebrickable-Nummer zurück)."""
        return self.bricklink_id or self.part_num

    @property
    def is_single_stud(self) -> bool:
        """True, wenn das Teil genau eine Noppe groß ist (1×1)."""
        return self.width_studs == 1 and self.length_studs == 1

    @property
    def area(self) -> int:
        """Fläche in Noppen."""
        return self.width_studs * self.length_studs


@dataclass(frozen=True, slots=True)
class PartCatalog:
    """Alles, was über verfügbare Teile bekannt ist.

    Attributes:
        colors: Farben nach Rebrickable-ID.
        part_types: Teiltypen nach Teilenummer (nur Teile aus der Geometrie-Whitelist).
        available: Menge der Kombinationen (Teilenummer, Farb-ID), die es wirklich gibt.
        element_ids: Erste bekannte LEGO-Element-ID pro Kombination (für die Teileliste).
    """

    colors: Mapping[int, LegoColor]
    part_types: Mapping[str, PartType]
    available: frozenset[tuple[str, int]]
    element_ids: Mapping[tuple[str, int], str] = field(default_factory=dict)

    def is_available(self, part_num: str, color_id: int) -> bool:
        """Prüft, ob es das Teil in dieser Farbe gibt."""
        # "in" auf einem frozenset ist eine Hash-Abfrage: O(1), unabhängig von der Größe
        return (part_num, color_id) in self.available

    def parts_in_color(self, color_id: int, kinds: Iterable[PartKind]) -> tuple[PartType, ...]:
        """Alle Teiltypen der erlaubten Arten, die es in dieser Farbe gibt.

        Die Reihenfolge ist deterministisch (nach Teilenummer sortiert).
        """
        allowed = frozenset(kinds)
        # Generator-Ausdruck mit zwei Bedingungen: Teilart erlaubt UND in der Farbe verfügbar
        matching = (
            part
            for part in self.part_types.values()
            if part.kind in allowed and self.is_available(part.part_num, color_id)
        )
        return tuple(sorted(matching, key=lambda part: part.part_num))

    def element_id(self, part_num: str, color_id: int) -> str | None:
        """Element-ID der Kombination oder ``None``, wenn keine bekannt ist."""
        return self.element_ids.get((part_num, color_id))


@dataclass(frozen=True, slots=True)
class Placement:
    """Ein platziertes Teil im Mosaik.

    Konvention: ``row`` = Bildzeile (oben = 0), ``col`` = Bildspalte (links = 0).
    ``rotated=False`` bedeutet: Die lange Seite liegt entlang der Spalten (x-Richtung).

    Attributes:
        part: Der Teiltyp.
        color: Die Farbe des Teils.
        row: Oberste Zeile, die das Teil belegt.
        col: Linke Spalte, die das Teil belegt.
        rotated: True, wenn die lange Seite entlang der Zeilen (nach unten) liegt.
        bottom_ldu: Höhe der Unterkante über der Oberseite des Unterbaus in LDU
            (0 = direkt auf Grundplatte/Paneel; ein erhöhter Rahmen liegt höher).
    """

    part: PartType
    color: LegoColor
    row: int
    col: int
    rotated: bool = False
    bottom_ldu: int = 0

    @property
    def top_ldu(self) -> int:
        """Höhe der Oberkante über dem Unterbau in LDU."""
        return self.bottom_ldu + self.part.height_ldu

    @property
    def cols_spanned(self) -> int:
        """Anzahl Spalten, die das Teil belegt (Ausdehnung in x-Richtung)."""
        # Bedingter Ausdruck: "a if Bedingung else b" wählt einen von zwei Werten
        return self.part.width_studs if self.rotated else self.part.length_studs

    @property
    def rows_spanned(self) -> int:
        """Anzahl Zeilen, die das Teil belegt (Ausdehnung nach unten)."""
        return self.part.length_studs if self.rotated else self.part.width_studs

    def cells(self) -> Iterable[tuple[int, int]]:
        """Alle belegten Zellen als (Zeile, Spalte)."""
        for row in range(self.row, self.row + self.rows_spanned):
            for col in range(self.col, self.col + self.cols_spanned):
                # yield macht die Methode zum Generator: Zellen entstehen erst beim Iterieren
                yield row, col


# eq=False: Ein NumPy-Array lässt sich nicht sinnvoll mit == zu einem bool vergleichen,
# deshalb verzichten wir auf den generierten Gleichheitsvergleich (Identität reicht).
@dataclass(frozen=True, slots=True, eq=False)
class Mosaic:
    """Das fertige Mosaik, aufgeteilt in Lagen in Bau-Reihenfolge.

    Von unten nach oben: Unterbau (Grundplatten oder Art-Paneele) → Verbinder
    (Technic-Pins zwischen den Paneelen) → Stützteile unter einem erhöhten Rahmen →
    Rahmen → Bild. Rahmen und Bild bilden zusammen die sichtbare Oberfläche.

    Attributes:
        title: Anzeigename, meist der Bilddateiname ohne Endung.
        width: Breite in Noppen.
        height: Höhe in Noppen.
        color_grid: H×W-Raster mit Indizes in ``palette`` (sichtbare Oberfläche inkl. Rahmen).
        palette: Die beim Dithering verwendeten Farben (plus Rahmenfarben).
        placements: Die Teile des Bilds innerhalb des Rahmens (werden pro Abschnitt gebaut).
        frame: Die sichtbaren Teile des Rahmens.
        supports: Unsichtbare Plates unter einem erhöhten Rahmen.
        substrate: Grundplatten bzw. Art-Paneele unter dem Mosaik (leer = ohne Unterbau).
        connectors: Technic-Pins, die benachbarte Art-Paneele verbinden.
        element_ids: LEGO-Element-IDs der genutzten (Teilenummer, Farb-ID)-Kombinationen.
        metadata: Zusatzinfos für Ausgaben (z. B. Konfiguration und Statistik als JSON-fähige
            Werte). Die Domain kennt deren Aufbau nicht, sie reicht sie nur durch.
    """

    title: str
    width: int
    height: int
    color_grid: IndexGrid
    palette: tuple[LegoColor, ...]
    placements: tuple[Placement, ...]
    frame: tuple[Placement, ...] = ()
    supports: tuple[Placement, ...] = ()
    substrate: tuple[Placement, ...] = ()
    connectors: tuple[Placement, ...] = ()
    element_ids: Mapping[tuple[str, int], str] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def surface(self) -> tuple[Placement, ...]:
        """Alle von oben sichtbaren Teile: Rahmen und Bild (z. B. für die Vorschau)."""
        return self.frame + self.placements

    @property
    def all_parts(self) -> tuple[Placement, ...]:
        """Alle Teile aller Lagen zusammen (z. B. für Teilelisten)."""
        return self.substrate + self.connectors + self.supports + self.surface

    def color_at(self, row: int, col: int) -> LegoColor:
        """Farbe der Zelle (Zeile, Spalte)."""
        return self.palette[int(self.color_grid[row, col])]

    def used_colors(self) -> tuple[LegoColor, ...]:
        """Tatsächlich sichtbare Farben, sortiert nach Rebrickable-ID."""
        # set-Comprehension entfernt Duplikate, sorted macht die Reihenfolge deterministisch
        used = {placement.color for placement in self.surface}
        return tuple(sorted(used, key=lambda color: color.id))


@dataclass(frozen=True, slots=True)
class PartsListEntry:
    """Eine Zeile der Teileliste: so viele Stück dieses Teils in dieser Farbe."""

    part: PartType
    color: LegoColor
    quantity: int


@dataclass(frozen=True, slots=True)
class PartsList:
    """Aggregierte Stückzahlen pro (Teil, Farbe), sortiert nach Farbe, dann Teil."""

    entries: tuple[PartsListEntry, ...]

    @classmethod
    def from_placements(cls, placements: Iterable[Placement]) -> PartsList:
        """Zählt die Platzierungen pro (Teil, Farbe) zusammen.

        ``@classmethod`` als Factory Method: ein alternativer Konstruktor, der die
        Liste aus Rohdaten berechnet, statt sie fertig übergeben zu bekommen.
        """
        # Counter ist ein dict, das zählt: counts[schlüssel] += 1 für jedes Vorkommen
        counts = Counter((placement.part, placement.color) for placement in placements)
        entries = (
            PartsListEntry(part=part, color=color, quantity=quantity)
            for (part, color), quantity in counts.items()
        )
        return cls(entries=tuple(sorted(entries, key=_parts_list_sort_key)))

    @property
    def total_quantity(self) -> int:
        """Gesamtzahl aller Teile."""
        return sum(entry.quantity for entry in self.entries)


def _parts_list_sort_key(entry: PartsListEntry) -> tuple[int, str]:
    """Sortierschlüssel: Tupel werden elementweise verglichen, also erst Farbe, dann Teil."""
    return entry.color.id, entry.part.part_num


@dataclass(frozen=True, slots=True)
class TilingStats:
    """Kennzahlen der Zerlegung.

    Attributes:
        pixels: Anzahl Noppen (Breite × Höhe).
        parts: Anzahl verwendeter Teile.
        savings_percent: Ersparnis gegenüber lauter 1×1-Teilen in Prozent.
    """

    pixels: int
    parts: int
    savings_percent: float

    @classmethod
    def from_placements(
        cls, width: int, height: int, placements: Iterable[Placement]
    ) -> TilingStats:
        """Berechnet die Kennzahlen aus den Platzierungen."""
        pixels = width * height
        parts = sum(1 for _ in placements)  # zählt auch Generatoren, nicht nur Listen
        savings = (1 - parts / pixels) * 100 if pixels else 0.0
        return cls(pixels=pixels, parts=parts, savings_percent=round(savings, 1))
