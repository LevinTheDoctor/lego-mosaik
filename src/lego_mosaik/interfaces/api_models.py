"""Antwortmodelle und Auswahllisten der HTTP-API (Pydantic).

Die Feldbeschreibungen erscheinen in ``/docs`` und in ``openapi.yaml``. Damit ein
Frontend Formulare bauen und Antworten typisieren kann, beschreibt jedes Modell genau
die Felder, die der Server liefert (``tests/test_api.py`` prüft das gegen echte Läufe).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from lego_mosaik.config import (
    COLOR_DISTANCE_LABELS,
    FIT_MODE_LABELS,
    MAX_FRAME_RAISE,
    RELIEF_LABELS,
    SUBSTRATE_LABELS,
    AppConfig,
    label_of,
)
from lego_mosaik.domain.layout import FORMATS, MAX_STUDS, MIN_STUDS
from lego_mosaik.domain.models import MOSAIC_PART_KINDS, ColorCategory
from lego_mosaik.domain.relief import MAX_RELIEF_LEVELS

PLATE_SIZES = (16, 32, 48)  # Kantenlängen von Grundplatten und großen Platten in Noppen

type JsonDict = dict[str, Any]
type OptionValue = str | int | float | bool | None


# --- Fehler, Katalog und Farben ------------------------------------------------------


class ErrorModel(BaseModel):
    fehler: str = Field(description="Deutsche Fehlermeldung, zum direkten Anzeigen gedacht")


class CatalogFileModel(BaseModel):
    datei: str = Field(description="Dateiname der Rebrickable-CSV")
    stand: str | None = Field(description="Letzte Änderung (ISO 8601, UTC) oder null")
    veraltet: bool = Field(description="True, wenn die Datei fehlt oder zu alt ist")


class HealthModel(BaseModel):
    status: str = Field(description="„ok“, wenn der Server läuft")
    katalog: list[CatalogFileModel] = Field(description="Stand der Katalogdateien")


class ColorModel(BaseModel):
    id: int = Field(description="Rebrickable-Farb-ID")
    name: str = Field(description="Farbname (Rebrickable)")
    hex: str = Field(description="Farbwert als #RRGGBB")
    kategorie: str = Field(description="Oberfläche: solid, metallic, pearl, …")
    ldraw_code: int | None = Field(description="LDraw-Farbcode")
    bricklink_code: int | None = Field(description="BrickLink-Farbcode, falls bekannt")
    lego_name: str | None = Field(description="Offizieller LEGO-Farbname, falls bekannt")


class UpdateFileModel(BaseModel):
    datei: str = Field(description="Dateiname")
    status: str = Field(description="heruntergeladen, aktuell oder fehlgeschlagen")
    hinweis: str = Field(description="Erklärung, bei Fehlern der Grund")


# --- Mosaike ---------------------------------------------------------------------------


class StatsModel(BaseModel):
    """Kennzahlen eines Mosaiks (``statistik`` in ``ergebnis.json``)."""

    breite: int = Field(description="Gesamtbreite in Noppen, mit Rahmen")
    hoehe: int = Field(description="Gesamthöhe in Noppen, mit Rahmen")
    bild_breite: int = Field(description="Breite der Bildfläche in Noppen (ohne Rahmen)")
    bild_hoehe: int = Field(description="Höhe der Bildfläche in Noppen (ohne Rahmen)")
    teile: int = Field(description="Alle Teile: Bild, Rahmen, Unterbau, Relief, Verbinder")
    farben: int = Field(description="Farben in der Teileliste (wie auf dem Titelblatt)")
    altfarben: int = Field(description="Davon Farben, die in aktuellen Sets nicht mehr vorkommen")
    anteil_1x1: float = Field(description="Anteil eckiger 1×1-Teile an allen Teilen (0 bis 1)")
    pixels: int = Field(description="Noppen der sichtbaren Oberfläche (Breite × Höhe)")
    parts: int = Field(description="Teile der sichtbaren Oberfläche")
    savings_percent: float = Field(description="Ersparnis gegenüber lauter 1×1-Teilen in %")
    stuetzteile: int = Field(description="Plates unter einem erhöhten Rahmen")
    formteile: int = Field(description="Runde und schräge Teile im Bild")
    relief_teile: int = Field(description="Stützteile unter erhöhten Bildteilen")
    unterlagen: int = Field(description="Farbige Plates unter Formteilen")
    unterbau_platten: int = Field(description="Grundplatten, große Platten oder Art-Paneele")
    verbinder: int = Field(description="Technic-Pins zwischen Art-Paneelen")
    verbindungsteile: int = Field(description="Plates, die große Platten verbinden")
    seiten: int = Field(description="Seiten der Bauanleitung (0 ohne Anleitung)")
    dauer_sekunden: dict[str, float] = Field(description="Dauer je Verarbeitungsschritt")


class PaletteColorModel(BaseModel):
    id: int = Field(description="Rebrickable-Farb-ID")
    name: str = Field(description="Farbname (Rebrickable)")
    hex: str = Field(description="Farbwert als #RRGGBB")
    ldraw_code: int | None = Field(description="LDraw-Farbcode")
    noppen: int = Field(description="Sichtbare Noppen in dieser Farbe")


class MosaicCreatedModel(BaseModel):
    id: str = Field(description="ID des Mosaiks (Name des Ausgabeordners)")
    statistik: StatsModel = Field(description="Kennzahlen des Mosaiks")
    dateien: dict[str, str] = Field(description="Dateiname → absolute Download-URL")


class ResultModel(BaseModel):
    """Inhalt von ``ergebnis.json``."""

    titel: str = Field(description="Name des Mosaiks (Titelblatt)")
    breite: int = Field(description="Gesamtbreite in Noppen")
    hoehe: int = Field(description="Gesamthöhe in Noppen")
    konfiguration: dict[str, Any] = Field(
        description="Vollständige Konfiguration des Laufs (Standardwerte plus Angaben)"
    )
    statistik: StatsModel = Field(description="Kennzahlen des Mosaiks")
    anzahl_teile: int = Field(description="Teile laut Teileliste (Summe aller Mengen)")
    palette: list[PaletteColorModel] = Field(description="Verwendete Farben")
    dateien: list[str] = Field(
        description="Dateinamen; herunterladen über /mosaike/{id}/dateien/{name}"
    )


# --- Auswahllisten und Standardwerte ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldOption:
    """Erlaubte Werte und Standard eines Formularfelds von ``POST /mosaike``.

    Attributes:
        default: Wert, der ohne Angabe gilt (aus der Konfiguration).
        values: Erlaubte Werte; leer = frei (Zahl oder Text).
        multiple: Kommagetrennte Liste aus ``values`` statt eines einzelnen Werts.
        minimum: Kleinster erlaubter Wert (Zahlen).
        maximum: Größter erlaubter Wert (Zahlen).
    """

    default: OptionValue = None
    values: tuple[str | int, ...] = ()
    multiple: bool = False
    minimum: float | None = None
    maximum: float | None = None

    def schema(self, json_type: str) -> JsonDict:
        """OpenAPI-Schema des optionalen Felds mit ``enum``, ``minimum`` und ``maximum``.

        Nur Beschreibung: Geprüft wird weiterhin in ``config.validate_config``, damit
        CLI und API dieselben deutschen Fehlermeldungen liefern. Pydantic übernimmt das
        dict als ``json_schema_extra`` und ersetzt damit sein ``anyOf`` aus Typ und null.
        """
        branch: JsonDict = {"type": json_type}
        if self.values and not self.multiple:
            branch["enum"] = list(self.values)
        if self.minimum is not None:
            branch["minimum"] = self.minimum
        if self.maximum is not None:
            branch["maximum"] = self.maximum
        return {"anyOf": [branch, {"type": "null"}]}

    def describe(self, description: str) -> str:
        """Feldbeschreibung plus erlaubte Werte einer Liste und Standardwert."""
        if self.multiple:
            description += f". Kommagetrennt aus: {', '.join(map(str, self.values))}"
        if self.default is not None:
            description += f" (Standard: {_text(self.default)})"
        return description


def _text(value: OptionValue) -> str:
    """Standardwert für Menschen: ja/nein statt True/False."""
    if isinstance(value, bool):
        return "ja" if value else "nein"
    return str(value)


class FieldOptionModel(BaseModel):
    standard: OptionValue = Field(description="Wert ohne Angabe; null = ergibt sich")
    werte: list[str | int] = Field(description="Erlaubte Werte; leer = frei")
    mehrfach: bool = Field(description="Kommagetrennte Liste aus ``werte``")
    minimum: float | None = Field(description="Kleinster erlaubter Wert")
    maximum: float | None = Field(description="Größter erlaubter Wert")


class FormatModel(BaseModel):
    name: str = Field(description="Wert für das Feld ``format``")
    platten_breite: int = Field(description="Platten nebeneinander")
    platten_hoehe: int = Field(description="Platten untereinander")
    beschreibung: str = Field(description="Kurze Beschreibung")


class OptionsModel(BaseModel):
    felder: dict[str, FieldOptionModel] = Field(
        description="Formularfeld von POST /mosaike → Standard und erlaubte Werte"
    )
    formate: list[FormatModel] = Field(description="Formate wie bei LEGO Art")


def form_options(
    config: AppConfig, method_names: Mapping[str, tuple[str, ...]]
) -> dict[str, FieldOption]:
    """Standardwerte und erlaubte Werte aller Auswahlfelder von ``POST /mosaike``.

    Registry-Namen (Stile, Dithering, Zerlegung, Ausgaben) kommen aus ``method_names``,
    damit neue Verfahren ohne Änderung hier erscheinen.
    """
    part_kinds = tuple(kind.value for kind in MOSAIC_PART_KINDS)
    frame_kinds = config.frame.part_kinds
    max_colors = config.palette.max_colors
    return {
        "unterbau": FieldOption(
            label_of(SUBSTRATE_LABELS, config.layout.substrate), tuple(SUBSTRATE_LABELS)
        ),
        "plattengroesse": FieldOption(config.layout.plate_studs, PLATE_SIZES),
        "format": FieldOption(None, tuple(art_format.name for art_format in FORMATS)),
        "platten": FieldOption(f"{config.layout.plates_wide}x{config.layout.plates_high}"),
        "breite": FieldOption(config.layout.width_studs, minimum=MIN_STUDS, maximum=MAX_STUDS),
        "hoehe": FieldOption(config.layout.height_studs, minimum=MIN_STUDS, maximum=MAX_STUDS),
        "unterbaufarbe": FieldOption(config.layout.substrate_color_id),
        "rahmen": FieldOption(config.frame.width, minimum=0),
        "rahmenfarbe": FieldOption(config.frame.color_id),
        "eckenfarbe": FieldOption(config.frame.corner_color_id),
        "rahmen_teile": FieldOption(
            ",".join(kind.value for kind in frame_kinds) if frame_kinds else None,
            part_kinds,
            multiple=True,
        ),
        "rahmen_erhoehung": FieldOption(
            config.frame.raise_plates, minimum=0, maximum=MAX_FRAME_RAISE
        ),
        "modus": FieldOption(
            label_of(FIT_MODE_LABELS, config.mosaic.fit_mode), tuple(FIT_MODE_LABELS)
        ),
        "kontrast": FieldOption(config.mosaic.contrast, minimum=0),
        "saettigung": FieldOption(config.mosaic.saturation, minimum=0),
        "teile": FieldOption(
            ",".join(kind.value for kind in config.palette.part_kinds), part_kinds, multiple=True
        ),
        "farbkategorien": FieldOption(
            ",".join(config.palette.color_categories),
            tuple(category.value for category in ColorCategory),
            multiple=True,
        ),
        "exotische_farben": FieldOption(False),
        "max_farben": FieldOption(max_colors or 0, minimum=0, maximum=MAX_STUDS * MAX_STUDS),
        "stil": FieldOption(config.shapes.style, method_names.get("Stile", ())),
        "relief": FieldOption(label_of(RELIEF_LABELS, config.relief.mode), tuple(RELIEF_LABELS)),
        "relief_hoehe": FieldOption(config.relief.levels, minimum=1, maximum=MAX_RELIEF_LEVELS),
        "dithering": FieldOption(config.dithering.method, method_names.get("Dithering", ())),
        "staerke": FieldOption(config.dithering.strength, minimum=0, maximum=1),
        "farbabstand": FieldOption(
            label_of(COLOR_DISTANCE_LABELS, config.dithering.color_distance),
            tuple(COLOR_DISTANCE_LABELS),
        ),
        "aufraeumen": FieldOption(config.dithering.despeckle),
        "zerlegung": FieldOption(config.tiling.strategy, method_names.get("Zerlegung", ())),
        "ausgaben": FieldOption(
            ",".join(config.outputs), method_names.get("Ausgaben", ()), multiple=True
        ),
        "titel": FieldOption(None),
    }


def options_model(options: Mapping[str, FieldOption]) -> OptionsModel:
    """Antwort von ``GET /optionen``."""
    fields = {
        name: FieldOptionModel(
            standard=option.default,
            werte=list(option.values),
            mehrfach=option.multiple,
            minimum=option.minimum,
            maximum=option.maximum,
        )
        for name, option in options.items()
    }
    formats = [
        FormatModel(
            name=f.name,
            platten_breite=f.plates_wide,
            platten_hoehe=f.plates_high,
            beschreibung=f.description,
        )
        for f in FORMATS
    ]
    return OptionsModel(felder=fields, formate=formats)
