"""Teilekatalog aus den Rebrickable-CSV-Dumps (gzip) und der Geometrie-Tabelle.

Gelesen wird immer über die Spaltennamen im Header (``csv.DictReader``), nie über
die Position. Zusätzliche Spalten, die Rebrickable später ergänzt, stören dadurch nicht.
"""

import csv
import gzip
import logging
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TextIO

from lego_mosaik.domain.errors import CatalogError
from lego_mosaik.domain.models import (
    ColorCategory,
    LegoColor,
    LongAxis,
    PartCatalog,
    PartKind,
    PartType,
)
from lego_mosaik.domain.palette import SPECIAL_COLOR_IDS
from lego_mosaik.sources.studio_colors import (
    StudioColor,
    StudioColorTable,
    category_from_keywords,
    read_studio_colors,
)

logger = logging.getLogger(__name__)

COLORS_FILE = "colors.csv.gz"
PARTS_FILE = "parts.csv.gz"
ELEMENTS_FILE = "elements.csv.gz"

# Benötigte Spalten pro Datei; weitere Spalten werden toleriert
COLOR_COLUMNS = ("id", "name", "rgb", "is_trans")
PART_COLUMNS = ("part_num", "name")
ELEMENT_COLUMNS = ("element_id", "part_num", "color_id")
GEOMETRY_COLUMNS = ("part_num", "kind", "width_studs", "length_studs", "ldraw_file", "height_ldu")
COLOR_MAPPING_COLUMNS = ("rebrickable_id", "ldraw_code")

# Texte, die in CSV-Dateien als "wahr" gelten (Rebrickable schreibt "True"/"False")
TRUE_VALUES = frozenset({"true", "t", "1", "yes"})
HEX_COLOR_LENGTH = 6
HEX_BASE = 16


@dataclass(frozen=True, slots=True)
class _Geometry:
    """Eine Zeile aus ``teil_geometrie.csv`` (noch ohne Teilname)."""

    kind: PartKind
    width_studs: int
    length_studs: int
    ldraw_file: str
    height_ldu: int
    ldraw_long_axis: LongAxis
    bricklink_id: str | None


class RebrickableCsvSource:
    """Lädt den ``PartCatalog`` aus lokalen CSV-Dateien und cacht ihn im Speicher.

    Erfüllt das Protocol ``PartCatalogSource`` (strukturell, ohne Vererbung).
    Die Klasse kennt die Konfiguration nicht, sondern bekommt nur die Pfade
    (Dependency Injection). Verdrahtet wird in ``main.py``.

    Args:
        data_dir: Ordner mit ``colors.csv.gz``, ``parts.csv.gz`` und ``elements.csv.gz``.
        geometry_file: Pfad zu ``teil_geometrie.csv`` (Whitelist der Teile).
        color_mapping_file: Pfad zu ``farb_zuordnung.csv`` (optional, darf fehlen).
        studio_color_file: Pfad zu Studios ``StudioColorDefinition.txt`` (optional).
        category_keywords: Fallback-Regeln für Farbkategorien ohne Studio-Tabelle.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        geometry_file: Path,
        color_mapping_file: Path,
        studio_color_file: Path | None,
        category_keywords: Sequence[tuple[str, ColorCategory]],
    ) -> None:
        self._data_dir = data_dir
        self._geometry_file = geometry_file
        self._color_mapping_file = color_mapping_file
        self._studio_color_file = studio_color_file
        self._category_keywords = tuple(category_keywords)
        self._catalog: PartCatalog | None = None
        # Lock: Im FastAPI-Threadpool könnten zwei Anfragen gleichzeitig zum ersten
        # Mal laden. "with self._lock" lässt immer nur einen Thread hinein.
        self._lock = threading.Lock()

    def load_catalog(self) -> PartCatalog:
        """Gibt den Katalog zurück; geparst wird nur beim ersten Aufruf (Lazy Loading).

        Raises:
            CatalogError: Wenn Dateien fehlen oder fehlerhaft sind.
        """
        with self._lock:
            if self._catalog is None:
                self._catalog = self._parse()
            return self._catalog

    def invalidate(self) -> None:
        """Verwirft den Cache, z. B. nach einer Aktualisierung der CSV-Dateien."""
        with self._lock:
            self._catalog = None

    def _parse(self) -> PartCatalog:
        """Liest alle Dateien und baut daraus den Katalog."""
        data_dir = self._data_dir
        geometry = read_geometry(self._geometry_file)
        enrichment = ColorEnrichment(
            ldraw_overrides=read_color_mapping(self._color_mapping_file),
            studio=read_studio_colors(self._studio_color_file),
            category_keywords=self._category_keywords,
        )
        if enrichment.studio is None:
            logger.warning(
                "Studio-Farbtabelle nicht gefunden: LDraw-Codes werden aus den Rebrickable-IDs "
                "übernommen (ungeprüft), BrickLink-Farbcodes fehlen."
            )
        colors = read_colors(data_dir / COLORS_FILE, enrichment)
        names = read_part_names(data_dir / PARTS_FILE, geometry.keys())
        part_types = {
            part_num: _to_part_type(part_num, names.get(part_num, part_num), row)
            for part_num, row in geometry.items()
        }
        available, element_ids = read_elements(
            data_dir / ELEMENTS_FILE, part_types.keys(), colors.keys()
        )
        logger.info(
            "Teilekatalog geladen: %d Farben, %d Teiltypen, %d verfügbare Kombinationen.",
            len(colors),
            len(part_types),
            len(available),
        )
        return PartCatalog(colors, part_types, available, element_ids)


def _to_part_type(part_num: str, name: str, row: _Geometry) -> PartType:
    """Verbindet Geometriezeile und Teilname zu einem ``PartType``."""
    return PartType(
        part_num=part_num,
        name=name,
        kind=row.kind,
        width_studs=row.width_studs,
        length_studs=row.length_studs,
        ldraw_file=row.ldraw_file,
        height_ldu=row.height_ldu,
        ldraw_long_axis=row.ldraw_long_axis,
        bricklink_id=row.bricklink_id,
    )


# --- Einzelne Dateien lesen -----------------------------------------------------


def read_geometry(path: Path) -> dict[str, _Geometry]:
    """Liest ``teil_geometrie.csv``: die Whitelist der verwendbaren Teile."""
    with _open_text(path, compressed=False) as handle:
        return dict(_parse_rows(handle, path, GEOMETRY_COLUMNS, _parse_geometry_row))


def _parse_geometry_row(row: dict[str, str]) -> tuple[str, _Geometry]:
    """Eine Geometriezeile → (Teilenummer, Geometrie)."""
    width, length = int(row["width_studs"]), int(row["length_studs"])
    if not 1 <= width <= length:
        raise ValueError("width_studs muss zwischen 1 und length_studs liegen")
    # row.get(..., "x"): Die Spalte ldraw_long_axis ist optional, Standard ist x
    geometry = _Geometry(
        kind=PartKind(row["kind"].strip().lower()),
        width_studs=width,
        length_studs=length,
        ldraw_file=row["ldraw_file"].strip(),
        height_ldu=int(row["height_ldu"]),
        ldraw_long_axis=LongAxis((row.get("ldraw_long_axis") or "x").strip().lower()),
        # optionale Spalte; leer oder fehlend → None (dann gilt die Rebrickable-Nummer)
        bricklink_id=(row.get("bricklink_id") or "").strip() or None,
    )
    return row["part_num"].strip(), geometry


def read_color_mapping(path: Path) -> dict[int, int]:
    """Liest die optionalen Overrides Rebrickable-ID → LDraw-Code. Fehlt die Datei: leer."""
    if not path.exists():
        return {}
    with _open_text(path, compressed=False) as handle:
        rows = _parse_rows(
            handle,
            path,
            COLOR_MAPPING_COLUMNS,
            lambda row: (int(row["rebrickable_id"]), int(row["ldraw_code"])),
        )
        return dict(rows)


@dataclass(frozen=True, slots=True)
class ColorEnrichment:
    """Ergänzt Rebrickable-Farben um LDraw-Code, Kategorie, BrickLink-Code und LEGO-Namen.

    Reihenfolge für den LDraw-Code:

    1. Override aus ``farb_zuordnung.csv``
    2. Namensabgleich mit Studios Farbtabelle (falls vorhanden)
    3. ohne Studio-Tabelle: Annahme LDraw-Code = Rebrickable-ID
    4. mit Studio-Tabelle, aber ohne Treffer: ``None`` (Farbe wird nicht verwendet)

    Attributes:
        ldraw_overrides: Rebrickable-ID → LDraw-Code.
        studio: Studios Farbtabelle oder ``None``.
        category_keywords: Fallback-Regeln für die Kategorie ohne Studio-Tabelle.
    """

    ldraw_overrides: Mapping[int, int]
    studio: StudioColorTable | None
    category_keywords: Sequence[tuple[str, ColorCategory]]

    def apply(self, color: LegoColor) -> LegoColor:
        """Gibt eine ergänzte Kopie der Farbe zurück."""
        match = self._studio_match(color)
        category = (match.category if match else None) or category_from_keywords(
            color.name, color.is_transparent, self.category_keywords
        )
        return replace(
            color,
            ldraw_code=self._ldraw_code(color, match),
            category=category,
            bricklink_code=match.bricklink_code if match else None,
            lego_name=match.lego_name if match else None,
        )

    def _studio_match(self, color: LegoColor) -> StudioColor | None:
        if self.studio is None:
            return None
        if color.id in self.ldraw_overrides:
            return self.studio.find_by_code(self.ldraw_overrides[color.id])
        return self.studio.find_by_name(color.name)

    def _ldraw_code(self, color: LegoColor, match: StudioColor | None) -> int | None:
        if color.id in self.ldraw_overrides:
            return self.ldraw_overrides[color.id]
        if self.studio is None:
            return color.id  # Annahme aus dem Prompt, ohne Studio nicht prüfbar
        return match.ldraw_code if match else None


def read_colors(path: Path, enrichment: ColorEnrichment) -> dict[int, LegoColor]:
    """Liest ``colors.csv.gz``. Die Sonderfarben -1 und 9999 werden immer ausgeschlossen."""

    def parse(row: dict[str, str]) -> LegoColor:
        color = LegoColor(
            id=int(row["id"]),
            name=row["name"].strip(),
            rgb=parse_hex_rgb(row["rgb"]),
            is_transparent=row["is_trans"].strip().lower() in TRUE_VALUES,
            ldraw_code=None,
        )
        return enrichment.apply(color)

    with _open_text(path, compressed=True) as handle:
        colors = _parse_rows(handle, path, COLOR_COLUMNS, parse)
        result = {color.id: color for color in colors if color.id not in SPECIAL_COLOR_IDS}
    unmapped = sorted(color.name for color in result.values() if color.ldraw_code is None)
    if unmapped:
        logger.info("%d Farben ohne sichere LDraw-Zuordnung werden nicht verwendet.", len(unmapped))
        logger.debug("Farben ohne LDraw-Zuordnung: %s", ", ".join(unmapped))
    return result


def read_part_names(path: Path, part_nums: Iterable[str]) -> dict[str, str]:
    """Liest die Namen der Whitelist-Teile aus ``parts.csv.gz``."""
    wanted = frozenset(part_nums)
    with _open_text(path, compressed=True) as handle:
        rows = _parse_rows(handle, path, PART_COLUMNS, lambda row: (row["part_num"], row["name"]))
        names = {part_num: name for part_num, name in rows if part_num in wanted}
    # Mengendifferenz: Teile aus der Whitelist, die Rebrickable nicht kennt
    for part_num in sorted(wanted - names.keys()):
        logger.warning("Teil %s aus der Geometrie-Tabelle fehlt in %s.", part_num, path.name)
    return names


def read_elements(
    path: Path, part_nums: Iterable[str], color_ids: Iterable[int]
) -> tuple[frozenset[tuple[str, int]], dict[tuple[str, int], str]]:
    """Liest ``elements.csv.gz``: welche (Teil, Farbe)-Kombinationen es gibt.

    Returns:
        Die Menge der Kombinationen und pro Kombination die kleinste Element-ID.
    """
    wanted_parts, wanted_colors = frozenset(part_nums), frozenset(color_ids)

    def parse(row: dict[str, str]) -> tuple[tuple[str, int], str]:
        return (row["part_num"].strip(), int(row["color_id"])), row["element_id"].strip()

    element_ids: dict[tuple[str, int], str] = {}
    with _open_text(path, compressed=True) as handle:
        for combo, element_id in _parse_rows(handle, path, ELEMENT_COLUMNS, parse):
            if combo[0] in wanted_parts and combo[1] in wanted_colors:
                current = element_ids.get(combo)
                # Deterministisch: immer die kleinste Element-ID behalten
                if current is None or _element_sort_key(element_id) < _element_sort_key(current):
                    element_ids[combo] = element_id
    return frozenset(element_ids), element_ids


def _element_sort_key(element_id: str) -> tuple[int, str]:
    """Numerische IDs numerisch vergleichen ("999" < "1000"), sonst als Text."""
    return (int(element_id), "") if element_id.isdigit() else (0, element_id)


def parse_hex_rgb(value: str) -> tuple[int, int, int]:
    """Wandelt ``"05131D"`` in ``(5, 19, 29)`` um.

    Raises:
        ValueError: Wenn der Wert kein sechsstelliger Hex-Wert ist.
    """
    text = value.strip().removeprefix("#")
    if len(text) != HEX_COLOR_LENGTH:
        raise ValueError(f"ungültiger Farbwert {value!r}")
    # Slicing [i:i+2] schneidet je zwei Zeichen heraus; int(..., 16) liest sie als Hex-Zahl
    red, green, blue = (int(text[index : index + 2], HEX_BASE) for index in (0, 2, 4))
    return red, green, blue


# --- Hilfsfunktionen für CSV-Zugriff --------------------------------------------


def _open_text(path: Path, *, compressed: bool) -> TextIO:
    """Öffnet eine (ggf. gzip-komprimierte) CSV-Datei als Text.

    Raises:
        CatalogError: Wenn die Datei fehlt.
    """
    if not path.exists():
        raise CatalogError(
            f"Die Datei {path} fehlt. Bitte zuerst „lego-mosaik teile-aktualisieren“ ausführen."
        )
    # newline="" ist für das csv-Modul vorgeschrieben, damit Zeilenumbrüche in
    # Anführungszeichen korrekt gelesen werden. "rt" = lesen im Textmodus.
    if compressed:
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open(encoding="utf-8", newline="")


def _parse_rows[T](
    handle: TextIO,
    path: Path,
    required: tuple[str, ...],
    parse: Callable[[dict[str, str]], T],
) -> Iterator[T]:
    """Liest alle Zeilen per Header-Namen und wandelt sie mit ``parse`` um.

    Fehlerhafte Zeilen ergeben einen ``CatalogError`` mit Dateiname und Zeilennummer.
    """
    reader = csv.DictReader(handle)
    _require_columns(reader.fieldnames, required, path)
    # enumerate(..., start=2): Zeile 1 ist der Header, Daten beginnen bei Zeile 2
    for line_number, row in enumerate(reader, start=2):
        try:
            yield parse(row)
        except (ValueError, KeyError, AttributeError) as error:
            # "from error" hängt den ursprünglichen Fehler als Ursache an (für --debug)
            raise CatalogError(f"{path.name}, Zeile {line_number}: {error}") from error


def _require_columns(
    fieldnames: Iterable[str] | None, required: tuple[str, ...], path: Path
) -> None:
    """Prüft, dass alle benötigten Spalten im Header stehen."""
    present = set(fieldnames or ())
    missing = [column for column in required if column not in present]
    if missing:
        raise CatalogError(f"In {path.name} fehlen die Spalten: {', '.join(missing)}.")
