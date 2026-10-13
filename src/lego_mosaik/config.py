"""Konfiguration als unveränderliche Dataclasses plus Overrides und Validierung.

Die konkreten Standardwerte stehen nicht hier, sondern in ``main.py`` (Composition
Root). Dieses Modul beschreibt nur die *Form* der Konfiguration und enthält die
einzige Stelle, an der Eingaben aus CLI und API geprüft und angewendet werden.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Protocol, get_type_hints

from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.layout import MAX_STUDS, MIN_STUDS, check_size, find_format, snap_to_plates
from lego_mosaik.domain.models import (
    MOSAIC_PART_KINDS,
    BackgroundMode,
    ColorCategory,
    ColorDistance,
    ColorFilter,
    FitMode,
    PartKind,
    PartShape,
    ReliefMode,
    Resampling,
    Smoothing,
    Substrate,
)
from lego_mosaik.domain.relief import MAX_RELIEF_LEVELS

MAX_PLATES = 16  # Unterbau-Platten pro Richtung
MAX_FRAME_RAISE = 3  # höchstens so viele Plates unter einem erhöhten Rahmen
MAX_PINS_PER_SEAM = 8
# Erlaubte Größen der Bayer-Matrix beim Ordered Dithering
BAYER_SIZES = (2, 4, 8)
# Gültiger Bereich für TCP-Ports
MIN_PORT = 1
MAX_PORT = 65535
# Stil ohne Formteile: nur eckige Teile wie bisher (Registry-Schlüssel in registry.SHAPERS)
PLAIN_STYLE = "quadrat"
# Stile, bei denen jede Noppe rund ist: Zwischen den Punkten muss ein Unterbau zu sehen sein
DOT_STYLES = frozenset({"punkte", "noppen"})
MAX_RECENT_YEARS = 100
MAX_MIN_COLOR_SHARE = 0.1  # mehr als 10 % Mindestanteil würde ganze Bildteile umfärben
MAX_SUBSAMPLE = 12  # Teilpixel pro Noppe und Richtung bei der Formwahl
# Origins lokaler Frontends (CORS): http(s)://localhost, 127.0.0.1 oder [::1] mit beliebigem Port
ORIGIN = re.compile(r"https?://[^/\s]+")  # ein einzelner Origin: Schema und Host, kein Pfad
LOCAL_ORIGINS = r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?"
# Werte der BrickLink-API (Price Guide)
CURRENCY = re.compile(r"[A-Z]{3}")  # ISO 4217
COUNTRY = re.compile(r"[A-Z]{2}")  # ISO 3166-1 alpha-2
BRICKLINK_REGIONS = (
    "africa",
    "asia",
    "eu",
    "europe",
    "middle_east",
    "north_america",
    "oceania",
    "south_america",
)
BRICKLINK_CONDITIONS = ("N", "U")
BRICKLINK_VAT = ("N", "Y", "O")
MAX_PARALLEL_REQUESTS = 16
# Bild bearbeiten
ROTATIONS = (0, 90, 180, 270)  # Drehung im Uhrzeigersinn in Grad
PERCENT = 100.0
CROP_VALUES = 4  # Ausschnitt: x, y, Breite, Höhe
MAX_BACKGROUND_TOLERANCE = 100.0  # ΔE; größer würde fast jedes Bild ganz zum Hintergrund


# Deutsche Bezeichnungen für CLI, API und interaktiven Modus → interne Enum-Werte
FIT_MODE_LABELS: Mapping[str, FitMode] = {"zuschneiden": FitMode.CROP, "einpassen": FitMode.PAD}
COLOR_DISTANCE_LABELS: Mapping[str, ColorDistance] = {
    "cie76": ColorDistance.CIE76,
    "ciede2000": ColorDistance.CIEDE2000,
}
SMOOTHING_LABELS: Mapping[str, Smoothing] = {
    "aus": Smoothing.NONE,
    "leicht": Smoothing.LIGHT,
    "mittel": Smoothing.MEDIUM,
    "stark": Smoothing.STRONG,
}
SUBSTRATE_LABELS: Mapping[str, Substrate] = {
    "art": Substrate.ART_PANEL,
    "grundplatte": Substrate.BASEPLATE,
    "platten": Substrate.PLATES,
    "ohne": Substrate.NONE,
}
RELIEF_LABELS: Mapping[str, ReliefMode] = {
    "ohne": ReliefMode.NONE,
    "motiv": ReliefMode.SUBJECT,
    "hell": ReliefMode.BRIGHT,
    "dunkel": ReliefMode.DARK,
}
COLOR_FILTER_LABELS: Mapping[str, ColorFilter] = {
    "ohne": ColorFilter.NONE,
    "graustufen": ColorFilter.GRAYSCALE,
    "sepia": ColorFilter.SEPIA,
    "warm": ColorFilter.WARM,
    "kalt": ColorFilter.COOL,
    "invertieren": ColorFilter.INVERT,
    "poster": ColorFilter.POSTERIZE,
    "vintage": ColorFilter.VINTAGE,
}
BACKGROUND_LABELS: Mapping[str, BackgroundMode] = {
    "behalten": BackgroundMode.KEEP,
    "entfernen": BackgroundMode.REMOVE,
    "aufhellen": BackgroundMode.LIGHTEN,
    "abdunkeln": BackgroundMode.DARKEN,
    "entsaettigen": BackgroundMode.DESATURATE,
    "weichzeichnen": BackgroundMode.BLUR,
}
# Standardfarbe des Unterbaus, wenn keine angegeben ist (Rebrickable-IDs)
DEFAULT_SUBSTRATE_COLOR_IDS: Mapping[Substrate, int] = {
    Substrate.BASEPLATE: 71,  # Light Bluish Gray
    Substrate.PLATES: 71,  # Light Bluish Gray: gibt es als 16er-Plate und 32er/48er-Grundplatte
    Substrate.ART_PANEL: 0,  # Black, wie bei LEGO Art
}


@dataclass(frozen=True, slots=True)
class LayoutConfig:
    """Größe und Unterbau.

    Größe entweder als Raster aus Platten (Gesamtgröße = Platten × Plattengröße)
    oder frei in Noppen (``width_studs``/``height_studs``). Mit Unterbau rastet eine
    freie Größe auf ganze Platten ein, damit keine Platte übersteht oder fehlt. Fehlt
    eine der beiden Seiten, ergibt sie sich aus dem Seitenverhältnis des Bilds.
    Formate (``layout.FORMATS``) setzen nur ``plates_wide`` und ``plates_high``.
    """

    substrate: Substrate = Substrate.BASEPLATE
    plate_studs: int = 32  # Grundplatte und große Platten 16, 32 oder 48; Art-Paneel 16
    plates_wide: int = 1  # Platten nebeneinander
    plates_high: int = 1  # Platten untereinander
    width_studs: int | None = None  # freie Breite in Noppen (None = aus den Platten)
    height_studs: int | None = None  # freie Höhe in Noppen
    substrate_color_id: int | None = None  # None = DEFAULT_SUBSTRATE_COLOR_IDS
    # Verbindung der Art-Paneele (Annahmen, in Studio prüfen: scripts/orientierung.py)
    pins_per_seam: int = 2  # Technic-Pins pro Naht zwischen zwei Paneelen
    connector_color_id: int = 0  # Black
    connector_depth_ldu: int = 16  # Lochmitte unter der Paneel-Oberkante (Paneel: 32 LDU)

    @property
    def custom_size(self) -> bool:
        """True, wenn die Größe frei in Noppen statt in Platten angegeben ist."""
        return self.width_studs is not None or self.height_studs is not None


def substrate_color_id(layout: LayoutConfig) -> int:
    """Farbe des Unterbaus: angegeben oder Standard für die Unterbau-Art."""
    if layout.substrate_color_id is not None:
        return layout.substrate_color_id
    return DEFAULT_SUBSTRATE_COLOR_IDS.get(layout.substrate, 0)


@dataclass(frozen=True, slots=True)
class FrameConfig:
    """Rahmen auf dem äußeren Ring des Mosaiks.

    ``raise_plates`` hebt den Rahmen wie bei LEGO Art etwas über das Bild: Unter die
    Rahmenteile kommen so viele Lagen Plates. Diese Lagen werden „auf Lücke“ gelegt
    und überbrücken die Nähte zwischen den Platten des Unterbaus.
    """

    width: int = 1  # Rahmenbreite in Noppen (0 = kein Rahmen)
    color_id: int = 0  # Rebrickable-ID 0 = Black
    corner_color_id: int | None = None  # eigene Farbe für die vier Ecken (None = wie Rahmen)
    part_kinds: tuple[PartKind, ...] | None = None  # z. B. nur TILE; None = wie das Bild
    raise_plates: int = 0  # 0 = bündig mit dem Bild, 1 = eine Plate höher, …


@dataclass(frozen=True, slots=True)
class MosaicConfig:
    """Bildaufbereitung vor dem Dithering.

    Reihenfolge: drehen und spiegeln → Ausschnitt → Hintergrund erkennen → Farbfilter →
    Helligkeit, Kontrast, Sättigung → Hintergrund anpassen → auf die Mosaikgröße bringen.
    """

    fit_mode: FitMode = FitMode.CROP
    pad_color_id: int = 15  # Rebrickable-ID 15 = White
    resampling: Resampling = Resampling.BOX
    contrast: float = 1.0  # 1.0 = unverändert
    saturation: float = 1.0  # 1.0 = unverändert
    brightness: float = 1.0  # 1.0 = unverändert
    color_filter: ColorFilter = ColorFilter.NONE
    filter_strength: float = 1.0  # 0.0 = Original, 1.0 = voller Filter
    rotate: int = 0  # Drehung im Uhrzeigersinn: 0, 90, 180 oder 270 Grad
    mirror: bool = False  # waagerecht spiegeln
    # Ausschnitt als (x, y, Breite, Höhe) in Prozent des gedrehten Bilds; None = ganzes Bild
    crop: tuple[float, float, float, float] | None = None
    background: BackgroundMode = BackgroundMode.KEEP
    background_color_id: int | None = None  # für „entfernen“; None = Randfarbe (pad_color_id)
    background_tolerance: float = 12.0  # ΔE: so ähnlich muss ein Pixel der Randfarbe sein
    background_amount: float = 0.5  # Stärke von aufhellen, abdunkeln, entsättigen, weichzeichnen


# "Exotische" Farben: alles außer Solid; Gummi (Rubber) taugt nie für Mosaike
EXOTIC_CATEGORIES: tuple[ColorCategory, ...] = tuple(
    category
    for category in ColorCategory
    if category not in {ColorCategory.SOLID, ColorCategory.RUBBER}
)

# Fallback-Regeln für Farbkategorien, wenn Studios Farbtabelle fehlt.
# Die Reihenfolge zählt: Das erste passende Schlüsselwort gewinnt.
DEFAULT_CATEGORY_KEYWORDS: tuple[tuple[str, ColorCategory], ...] = (
    ("Glitter", ColorCategory.GLITTER),
    ("Speckle", ColorCategory.SPECKLE),
    ("Satin", ColorCategory.SATIN),
    ("Opal", ColorCategory.SATIN),
    ("Glow", ColorCategory.MILKY),
    ("Milky", ColorCategory.MILKY),
    ("Chrome", ColorCategory.CHROME),
    ("Pearl", ColorCategory.PEARL),
    ("Flat Silver", ColorCategory.PEARL),
    ("Metallic", ColorCategory.METALLIC),
    ("Rubber", ColorCategory.RUBBER),
    ("Trans", ColorCategory.TRANSPARENT),
)


@dataclass(frozen=True, slots=True)
class PaletteConfig:
    """Welche Teilarten und Farben erlaubt sind."""

    part_kinds: tuple[PartKind, ...]
    color_categories: tuple[ColorCategory, ...] = (ColorCategory.SOLID,)
    include_color_ids: tuple[int, ...] = ()  # einzelne Farben trotz Kategorie erlauben
    exclude_color_ids: tuple[int, ...] = ()  # hat Vorrang vor allem anderen
    # Eigene Palette: nur diese Farben (sofern es sie für Teilarten und Stil gibt); None = alle
    only_color_ids: tuple[int, ...] | None = None
    max_colors: int | None = None  # None = keine Begrenzung
    # Farben, die nach dem Dithering auf weniger als diesem Anteil der Bildfläche liegen,
    # fallen weg; das Bild wird ohne sie neu gedithert (0 = alle Farben behalten)
    min_color_share: float = 0.0


@dataclass(frozen=True, slots=True)
class DitheringConfig:
    """Auswahl und Parameter des Dithering-Verfahrens."""

    method: str  # Schlüssel aus der Registry
    strength: float = 1.0  # 0.0 = kein Fehlertransport, 1.0 = voll
    color_distance: ColorDistance = ColorDistance.CIEDE2000
    serpentine: bool = True  # Zeilen abwechselnd links/rechts abarbeiten
    bayer_size: int = 4  # nur für "ordered": 2, 4 oder 8
    spread: float = 32.0  # nur für "ordered": Stärke des Schwellwert-Offsets in RGB-Stufen
    # Einzelne Pixel (kein Nachbar in derselben Farbe) durch die Nachbarfarbe ersetzen:
    # weniger Rauschen und 1×1-Teile, gut für Comics und Logos, weniger für Fotos
    despeckle: bool = False
    # Kleine Flecken ähnlicher Farben zusammenlegen, ab MEDIUM Mehrheitsfarbe je Noppe statt
    # Dithering; kontrastreiche Details (Augen, Pupillen) bleiben stehen
    smoothing: Smoothing = Smoothing.LIGHT


@dataclass(frozen=True, slots=True)
class TilingConfig:
    """Auswahl und Parameter der Zerlegung in Teile."""

    strategy: str  # Schlüssel aus der Registry
    max_part_length: int = 8
    # Jeden Bauabschnitt (siehe InstructionsConfig.section_studs) getrennt zerlegen:
    # Kein Teil ragt dann in einen anderen Abschnitt, jede Anleitungsseite ist für sich baubar
    split_sections: bool = True


@dataclass(frozen=True, slots=True)
class ShapeConfig:
    """Stil des Bilds: nur eckige Teile oder zusätzlich Formteile (rund, Viertelkreis, …).

    ``style`` ist ein Schlüssel aus ``registry.SHAPERS``:

    - ``quadrat``: nur Plates und Tiles, gleiche Farben werden zusammengefasst
    - ``formen``: an Kanten Viertel- und Halbkreise, Dreiecke und Wedge Plates, wo sie
      die Kontur genauer treffen (Bild wird dafür feiner ausgewertet)
    - ``punkte``: jede Noppe ein Tile Round 1 × 1 (Look von LEGO Art und Brick Sketches)
    - ``noppen``: jede Noppe eine Plate Round 1 × 1 („Studs“)

    In den Lücken der Formteile ist der Unterbau bzw. die Relief-Stützlage zu sehen.
    """

    style: str = PLAIN_STYLE
    shapes: tuple[PartShape, ...] = (
        PartShape.QUARTER,
        PartShape.HALF,
        PartShape.ROUND,
        PartShape.TRIANGLE,
        PartShape.CUT_CORNER,
        PartShape.WEDGE,
    )
    subsample: int = 6  # Teilpixel pro Noppe und Richtung (nur „formen“)
    min_gain: float = 0.2  # Formteil nur, wenn der Fehler um diesen Anteil sinkt
    min_improvement: float = 3.0  # … und im Mittel um so viel ΔE pro Teilpixel
    # „formen“: Ein Formteil darf auf einer Plate in zweiter Farbe liegen (eine Plate höher),
    # damit die Lücke die Nachbarfarbe zeigt statt des Unterbaus (wie Brick Sketches)
    underlay: bool = True


@dataclass(frozen=True, slots=True)
class ReliefConfig:
    """Relief wie bei Brick Sketches: Bildteile liegen auf Stützlagen aus Plates höher."""

    mode: ReliefMode = ReliefMode.NONE
    levels: int = 1  # höchste Stufe in Plates (1 bis 3)
    color_id: int | None = None  # Farbe der Stützlagen; None = Farbe des Unterbaus


@dataclass(frozen=True, slots=True)
class PreviewConfig:
    """Darstellung der Vorschau-PNG."""

    stud_px: int = 16  # Kantenlänge einer Noppe in Pixeln
    draw_part_edges: bool = True


@dataclass(frozen=True, slots=True)
class LdrawConfig:
    """Aufbau der LDraw-Datei."""

    rows_per_step: int = 4


@dataclass(frozen=True, slots=True)
class InstructionsConfig:
    """Aufbau der Bauanleitung: Querformat wie ein LEGO-Heft (A4 quer bei 150 dpi)."""

    section_studs: int = 16  # Abschnittsgröße in Noppen (quadratisch)
    page_width_px: int = 1754
    page_height_px: int = 1240
    margin_px: int = 50
    max_stud_px: int = 48  # größte Noppe auf der Abschnittskarte
    # Kamera der 3D-Ansichten: gerade von vorne, steil von oben (wie LEGO Art);
    # Zeilen und Spalten bleiben abzählbar. Drehung > 0 zeigt zusätzlich Seitenflächen.
    view_elevation_deg: float = 60.0
    view_azimuth_deg: float = 0.0
    supersample: int = 2  # intern größer rendern und verkleinern (glatte Kanten)
    step_render_height_px: int = 900  # höchstens so hoch ist eine Schrittansicht in Pixeln
    # TrueType-Schriften mit Umlauten; die erste vorhandene wird genutzt.
    # Pillows eingebaute Schrift kann weder ä/ö/ü noch × und –.
    font_paths: tuple[Path, ...] = (
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),  # macOS
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),  # Debian/Ubuntu
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),  # Arch
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),  # Windows
    )
    # Fette Schriften für Schrittnummern und Titel (wie in LEGO-Heften)
    bold_font_paths: tuple[Path, ...] = (
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),  # macOS
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),  # Debian/Ubuntu
        Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),  # Arch
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),  # Windows
    )


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """Datenquelle Rebrickable-CSV und deren Aktualisierung."""

    data_dir: Path
    geometry_file: Path = Path("daten/teil_geometrie.csv")
    color_mapping_file: Path = Path("daten/farb_zuordnung.csv")
    # Optional: Farbtabelle von Bricklink Studio (LDraw-/BrickLink-Codes, Kategorien)
    studio_color_file: Path | None = Path("/Applications/Studio 2.0/data/StudioColorDefinition.txt")
    category_keywords: tuple[tuple[str, ColorCategory], ...] = DEFAULT_CATEGORY_KEYWORDS
    base_url: str = "https://cdn.rebrickable.com/media/downloads/"
    files: tuple[str, ...] = ("colors.csv.gz", "parts.csv.gz", "elements.csv.gz")
    # Set-Inventare (ca. 15 MB): erkennen ausgelaufene Teil-Farb-Kombinationen. Fehlen sie,
    # läuft alles weiter; Altfarben werden dann nur über das letzte Jahr der Farbe erkannt.
    optional_files: tuple[str, ...] = (
        "sets.csv.gz",
        "inventories.csv.gz",
        "inventory_parts.csv.gz",
    )
    # Nur Teile in Farben aus Sets der letzten so vielen Jahre (None = auch historische)
    recent_years: int | None = 3
    max_age_days: int = 7
    auto_update: bool = True
    timeout_seconds: float = 60.0
    user_agent: str = "lego-mosaik/0.1 (lokaler MVP)"


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Lokaler HTTP-Server."""

    host: str = "127.0.0.1"
    port: int = 8000
    max_upload_mb: int = 20
    update_interval_hours: int = 24
    # Webseiten, die die API im Browser aufrufen dürfen (CORS): Regex für den Origin, z. B.
    # jeder Port auf localhost (Vite, React-Dev-Server), plus einzelne Origins. None/() = keine
    cors_origin_regex: str | None = LOCAL_ORIGINS
    cors_origins: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PricingConfig:
    """Preisschätzung über die BrickLink-API (Price Guide) und die Wanted List.

    Die Zugangsdaten stehen bewusst nicht hier, sondern in Umgebungsvariablen
    (siehe ``pricing/credentials.py``), damit sie nie in ``ergebnis.json`` landen.
    """

    base_url: str = "https://api.bricklink.com/api/store/v1"
    currency: str = "EUR"  # ISO 4217, z. B. EUR, USD, GBP
    region: str | None = "europe"  # Verkäufer aus dieser Region (None = weltweit)
    country_code: str | None = None  # oder nur aus einem Land, z. B. "DE"
    condition: str = "N"  # N = neu, U = gebraucht (auch <CONDITION> in der Wanted List)
    vat: str = "Y"  # Y = mit MwSt., N = ohne, O = mit norwegischer MwSt.
    cache_file: Path = Path("daten/bricklink/preise.json")
    max_age_days: int = 7  # ältere Preise werden neu abgefragt
    timeout_seconds: float = 30.0
    max_parallel: int = 4  # gleichzeitige Anfragen (BrickLink erlaubt 5.000 pro Tag)
    # Höchstpreis in der Wanted List = Ø Verkaufspreis × Faktor (None = ohne Höchstpreis)
    max_price_factor: float | None = None
    upload_url: str = "https://www.bricklink.com/v2/wanted/upload.page"


@dataclass(frozen=True, slots=True)
class StorageConfig:
    """Gespeicherte Bibliothek und was jeder Lauf zusätzlich aufhebt."""

    palettes_file: Path = Path("daten/paletten.json")  # eigene Farbpaletten
    presets_file: Path = Path("daten/presets.json")  # Presets mit Bewertung und Favoriten
    # Originalbild und Eingaben im Mosaik-Ordner speichern (nötig für „bearbeiten“)
    keep_source: bool = True


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Gesamte Konfiguration. Wird einmal in ``main.py`` erzeugt."""

    layout: LayoutConfig
    mosaic: MosaicConfig
    palette: PaletteConfig
    dithering: DitheringConfig
    tiling: TilingConfig
    outputs: tuple[str, ...]
    source: SourceConfig
    output_dir: Path
    frame: FrameConfig = field(default_factory=FrameConfig)
    shapes: ShapeConfig = field(default_factory=ShapeConfig)
    relief: ReliefConfig = field(default_factory=ReliefConfig)
    preview: PreviewConfig = field(default_factory=PreviewConfig)
    ldraw: LdrawConfig = field(default_factory=LdrawConfig)
    instructions: InstructionsConfig = field(default_factory=InstructionsConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    pricing: PricingConfig = field(default_factory=PricingConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)


def frame_part_kinds(config: AppConfig) -> tuple[PartKind, ...]:
    """Teilarten des Rahmens: eigene Angabe oder dieselben wie im Bild."""
    return config.frame.part_kinds or config.palette.part_kinds


def relief_color_id(config: AppConfig) -> int:
    """Farbe der Relief-Stützlagen: angegeben oder wie der Unterbau."""
    if config.relief.color_id is not None:
        return config.relief.color_id
    return substrate_color_id(config.layout)


@dataclass(frozen=True, slots=True)
class MosaicOverrides:
    """Werte, die CLI, API oder interaktiver Modus für einen einzelnen Lauf überschreiben.

    ``None`` heißt: Wert aus der Konfiguration übernehmen. Alle Oberflächen übersetzen
    ihre Eingaben in dieses eine Objekt (DRY), geprüft wird nur in ``apply_overrides``.
    """

    # Größe und Unterbau
    substrate: Substrate | None = None
    plate_studs: int | None = None
    plates_wide: int | None = None
    plates_high: int | None = None
    width_studs: int | None = None
    height_studs: int | None = None
    substrate_color_id: int | None = None
    # Rahmen
    frame_width: int | None = None
    frame_color_id: int | None = None
    frame_corner_color_id: int | None = None
    frame_part_kinds: tuple[PartKind, ...] | None = None
    frame_raise_plates: int | None = None
    # Bild
    fit_mode: FitMode | None = None
    contrast: float | None = None
    saturation: float | None = None
    brightness: float | None = None
    color_filter: ColorFilter | None = None
    filter_strength: float | None = None
    rotate: int | None = None
    mirror: bool | None = None
    crop: tuple[float, float, float, float] | None = None
    background: BackgroundMode | None = None
    background_color_id: int | None = None
    background_tolerance: float | None = None
    background_amount: float | None = None
    # Farben und Teile
    part_kinds: tuple[PartKind, ...] | None = None
    color_categories: tuple[ColorCategory, ...] | None = None
    max_colors: int | None = None  # 0 = keine Begrenzung
    only_color_ids: tuple[int, ...] | None = None
    # Stil und Relief
    style: str | None = None
    relief: ReliefMode | None = None
    relief_levels: int | None = None
    # Verfahren und Ausgaben
    dithering_method: str | None = None
    strength: float | None = None
    color_distance: ColorDistance | None = None
    despeckle: bool | None = None
    smoothing: Smoothing | None = None
    tiling_strategy: str | None = None
    outputs: tuple[str, ...] | None = None
    output_dir: Path | None = None


def apply_overrides(config: AppConfig, overrides: MosaicOverrides) -> AppConfig:
    """Erzeugt die Laufkonfiguration aus Standardwerten und Overrides und prüft sie.

    ``dataclasses.replace`` erzeugt eine Kopie mit geänderten Feldern. Das Original
    bleibt unverändert (frozen), mehrere Läufe beeinflussen sich also nicht.

    Raises:
        ConfigError: Wenn die resultierende Konfiguration ungültig ist.
    """
    o = overrides
    run_config = replace(
        config,
        layout=_merged(
            config.layout,
            substrate=o.substrate,
            plate_studs=o.plate_studs,
            plates_wide=o.plates_wide,
            plates_high=o.plates_high,
            width_studs=o.width_studs,
            height_studs=o.height_studs,
            substrate_color_id=o.substrate_color_id,
        ),
        frame=_merged(
            config.frame,
            width=o.frame_width,
            color_id=o.frame_color_id,
            corner_color_id=o.frame_corner_color_id,
            part_kinds=o.frame_part_kinds,
            raise_plates=o.frame_raise_plates,
        ),
        mosaic=_merged(
            config.mosaic,
            fit_mode=o.fit_mode,
            contrast=o.contrast,
            saturation=o.saturation,
            brightness=o.brightness,
            color_filter=o.color_filter,
            filter_strength=o.filter_strength,
            rotate=o.rotate,
            mirror=o.mirror,
            crop=o.crop,
            background=o.background,
            background_color_id=o.background_color_id,
            background_tolerance=o.background_tolerance,
            background_amount=o.background_amount,
        ),
        palette=_merged(
            config.palette,
            part_kinds=o.part_kinds,
            color_categories=o.color_categories,
            max_colors=o.max_colors,
            only_color_ids=o.only_color_ids,
        ),
        dithering=_merged(
            config.dithering,
            method=o.dithering_method,
            strength=o.strength,
            color_distance=o.color_distance,
            despeckle=o.despeckle,
            smoothing=o.smoothing,
        ),
        shapes=_merged(config.shapes, style=o.style),
        relief=_merged(config.relief, mode=o.relief, levels=o.relief_levels),
        tiling=_merged(config.tiling, strategy=o.tiling_strategy),
        outputs=_or_default(o.outputs, config.outputs),
        output_dir=_or_default(o.output_dir, config.output_dir),
    )
    if run_config.palette.max_colors == 0:  # 0 bedeutet „keine Begrenzung“
        run_config = replace(run_config, palette=replace(run_config.palette, max_colors=None))
    validate_config(run_config)
    return run_config


class _Dataclass(Protocol):
    """Alles, was ``@dataclass`` erzeugt hat (daran erkennt ``replace`` seine Argumente)."""

    __dataclass_fields__: ClassVar[dict[str, Any]]


def _merged[T: _Dataclass](target: T, **values: object) -> T:
    """Kopie von ``target``, in der nur Felder mit Override (nicht ``None``) ersetzt sind.

    ``[T: _Dataclass]`` ist ein Typparameter mit Obergrenze (PEP 695): Es geht jede
    Dataclass hinein, und genau deren Typ kommt wieder heraus. ``**values`` sammelt
    alle Schlüsselwort-Argumente in einem dict.
    """
    changes = {name: value for name, value in values.items() if value is not None}
    return replace(target, **changes)


def _or_default[T](value: T | None, default: T) -> T:
    """Den Override-Wert, oder den Standardwert, wenn der Override ``None`` ist.

    Bewusst ``is None`` statt ``value or default``: Sonst würden gültige "falsy"
    Werte wie ``0.0`` (Stärke) stillschweigend durch den Standard ersetzt.
    """
    return default if value is None else value


def validate_config(config: AppConfig) -> None:
    """Prüft alle Werte und meldet den ersten Fehler auf Deutsch.

    Registry-Namen (Dithering, Zerlegung, Ausgaben) prüft ``registry.py``,
    weil nur dort bekannt ist, welche Namen es gibt.

    Raises:
        ConfigError: Bei einem ungültigen Wert.
    """
    _check_frame(config.frame)
    _check_layout(config.layout, config.frame.width)
    _check_shapes(config)
    _check_range("Relief-Höhe", config.relief.levels, 1, MAX_RELIEF_LEVELS, "Plates")
    _check_range("Dithering-Stärke", config.dithering.strength, 0.0, 1.0)
    _check_image(config.mosaic)
    _check_part_kinds(config.palette.part_kinds)
    if config.palette.only_color_ids is not None:
        _check_unique_non_empty("Eigene Palette", tuple(map(str, config.palette.only_color_ids)))
    _check_unique_non_empty("Farbkategorien", config.palette.color_categories)
    _check_range(
        "Mindestanteil einer Farbe", config.palette.min_color_share, 0.0, MAX_MIN_COLOR_SHARE
    )
    if config.palette.max_colors is not None:
        _check_range("Maximale Farbanzahl", config.palette.max_colors, 1, MAX_STUDS * MAX_STUDS)
    if config.dithering.bayer_size not in BAYER_SIZES:
        raise ConfigError(f"Die Bayer-Matrix muss {_join(map(str, BAYER_SIZES))} groß sein.")
    _check_positive("Ordered-Dithering-Spreizung", config.dithering.spread, allow_zero=True)
    _check_range("Maximale Teillänge", config.tiling.max_part_length, 1, MAX_STUDS)
    _check_unique_non_empty("Ausgaben", config.outputs)
    _check_range("Zeilen pro Bauschritt", config.ldraw.rows_per_step, 1, MAX_STUDS)
    _check_range("Noppengröße der Vorschau", config.preview.stud_px, 1, MAX_STUDS)
    _check_range("Abschnittsgröße der Anleitung", config.instructions.section_studs, 1, MAX_STUDS)
    _check_range("Noppengröße der Anleitung", config.instructions.max_stud_px, 4, MAX_STUDS)
    _check_range("Kamera-Höhenwinkel", config.instructions.view_elevation_deg, 10.0, 90.0, "Grad")
    _check_range("Kamera-Drehung", config.instructions.view_azimuth_deg, 0.0, 45.0, "Grad")
    _check_range("Supersampling", config.instructions.supersample, 1, 4)
    if config.source.recent_years is not None:
        _check_range("Aktuelle Jahre", config.source.recent_years, 1, MAX_RECENT_YEARS)
    _check_range("Port", config.server.port, MIN_PORT, MAX_PORT)
    _check_range("Upload-Grenze", config.server.max_upload_mb, 1, 1024, "MB")
    _check_cors(config.server)
    _check_pricing(config.pricing)


def _check_range(label: str, value: float, minimum: float, maximum: float, unit: str = "") -> None:
    """Prüft ``minimum <= value <= maximum`` (verkettete Vergleiche wie in der Mathematik)."""
    if not minimum <= value <= maximum:
        suffix = f" {unit}" if unit else ""
        raise ConfigError(
            f"{label} muss zwischen {minimum} und {maximum}{suffix} liegen (angegeben: {value})."
        )


def _check_positive(label: str, value: float, *, allow_zero: bool = False) -> None:
    """Prüft, dass ein Wert größer als 0 ist (bzw. mindestens 0)."""
    if value < 0 or (value == 0 and not allow_zero):
        relation = "mindestens 0" if allow_zero else "größer als 0"
        raise ConfigError(f"{label} muss {relation} sein (angegeben: {value}).")


def _check_layout(layout: LayoutConfig, frame_width: int) -> None:
    """Plattenanzahl, Gesamtgröße und verbleibende Bildfläche."""
    _check_range("Platten nebeneinander", layout.plates_wide, 1, MAX_PLATES)
    _check_range("Platten untereinander", layout.plates_high, 1, MAX_PLATES)
    _check_range("Plattengröße", layout.plate_studs, 1, MAX_STUDS, "Noppen")
    _check_range("Pins pro Naht", layout.pins_per_seam, 1, MAX_PINS_PER_SEAM)
    if not layout.custom_size:
        check_size(
            layout.plate_studs * layout.plates_wide,
            layout.plate_studs * layout.plates_high,
            frame_width,
        )
        return
    # Freie Größe: angegebene Seiten jetzt prüfen; eine fehlende Seite ergibt sich
    # erst aus dem Bild und wird dann in der Pipeline geprüft
    for label, value in (("Breite", layout.width_studs), ("Höhe", layout.height_studs)):
        if value is not None:
            _check_range(label, value, MIN_STUDS, MAX_STUDS, "Noppen")
    if layout.width_studs is not None and layout.height_studs is not None:
        width, height = layout.width_studs, layout.height_studs
        if layout.substrate is not Substrate.NONE:  # wie in der Pipeline: auf ganze Platten
            width, height = (snap_to_plates(v, layout.plate_studs) for v in (width, height))
        check_size(width, height, frame_width)


def _check_shapes(config: AppConfig) -> None:
    """Formteile gehen nur mit flachen Teilen; Punkte brauchen einen Unterbau dazwischen.

    „formen“ geht auch ohne Unterbau: Dann liegt jedes Formteil auf einer Unterlage.
    """
    shapes = config.shapes
    _check_range("Teilpixel der Formwahl", shapes.subsample, 2, MAX_SUBSAMPLE)
    _check_range("Mindestgewinn der Formwahl", shapes.min_gain, 0.0, 1.0)
    _check_positive("Mindestverbesserung der Formwahl", shapes.min_improvement, allow_zero=True)
    _check_unique_non_empty("Formteile", shapes.shapes)
    if PartShape.SQUARE in shapes.shapes:
        raise ConfigError("Formteile: „square“ ist kein Formteil.")
    if shapes.style == PLAIN_STYLE:
        return
    if shapes.style in DOT_STYLES and config.layout.substrate is Substrate.NONE:
        raise ConfigError(
            f"Der Stil „{shapes.style}“ braucht einen Unterbau (art, platten oder grundplatte): "
            "Zwischen den runden Teilen ist er zu sehen."
        )
    if PartKind.BRICK in config.palette.part_kinds:
        raise ConfigError(
            f"Der Stil „{shapes.style}“ geht nur mit Plates und Tiles, nicht mit Bricks."
        )


def _check_cors(server: ServerConfig) -> None:
    """Der CORS-Regex muss sich übersetzen lassen, Origins sind ``schema://host[:port]``."""
    if server.cors_origin_regex is not None:
        try:
            re.compile(server.cors_origin_regex)
        except re.error as error:
            raise ConfigError(f"CORS-Regex ungültig: {error}.") from None
    for origin in server.cors_origins:
        if not ORIGIN.fullmatch(origin):
            raise ConfigError(
                f"CORS-Origin „{origin}“ ungültig. Beispiel: http://localhost:5173 (ohne /)."
            )


def _check_pricing(pricing: PricingConfig) -> None:
    """Währung, Region, Land, Zustand und MwSt. in der Schreibweise der BrickLink-API."""
    if not CURRENCY.fullmatch(pricing.currency):
        raise ConfigError(f"Währung „{pricing.currency}“ ungültig. Beispiel: EUR, USD, GBP.")
    if pricing.region is not None and pricing.region not in BRICKLINK_REGIONS:
        raise ConfigError(
            f"Region „{pricing.region}“ unbekannt. Gültig sind: {_join(BRICKLINK_REGIONS)}."
        )
    if pricing.country_code is not None and not COUNTRY.fullmatch(pricing.country_code):
        raise ConfigError(f"Land „{pricing.country_code}“ ungültig. Beispiel: DE, AT, CH.")
    if pricing.condition not in BRICKLINK_CONDITIONS:
        raise ConfigError("Der Zustand muss N (neu) oder U (gebraucht) sein.")
    if pricing.vat not in BRICKLINK_VAT:
        raise ConfigError("Die MwSt.-Angabe muss Y (mit), N (ohne) oder O (Norwegen) sein.")
    _check_range("Höchstalter der Preise", pricing.max_age_days, 0, MAX_RECENT_YEARS * 365, "Tage")
    _check_positive("Timeout der Preisabfrage", pricing.timeout_seconds)
    _check_range("Gleichzeitige Preisabfragen", pricing.max_parallel, 1, MAX_PARALLEL_REQUESTS)
    if pricing.max_price_factor is not None:
        _check_positive("Faktor für den Höchstpreis", pricing.max_price_factor)


def _check_image(mosaic: MosaicConfig) -> None:
    """Werte der Bildbearbeitung: Faktoren, Drehung, Ausschnitt und Hintergrund."""
    _check_positive("Kontrast", mosaic.contrast)
    _check_positive("Sättigung", mosaic.saturation)
    _check_positive("Helligkeit", mosaic.brightness)
    _check_range("Filterstärke", mosaic.filter_strength, 0.0, 1.0)
    if mosaic.rotate not in ROTATIONS:
        raise ConfigError(
            f"Drehen geht nur um {_join(map(str, ROTATIONS))} Grad (angegeben: {mosaic.rotate})."
        )
    if mosaic.crop is not None:
        x, y, width, height = mosaic.crop
        for label, value in (("x", x), ("y", y), ("Breite", width), ("Höhe", height)):
            _check_range(f"Ausschnitt {label}", value, 0.0, PERCENT, "%")
        if width <= 0 or height <= 0 or x + width > PERCENT or y + height > PERCENT:
            raise ConfigError(
                "Der Ausschnitt muss im Bild liegen und eine Fläche haben "
                f"(x + Breite und y + Höhe höchstens {PERCENT:g} %)."
            )
    _check_range(
        "Hintergrund-Toleranz", mosaic.background_tolerance, 0.0, MAX_BACKGROUND_TOLERANCE, "ΔE"
    )
    _check_range("Hintergrund-Stärke", mosaic.background_amount, 0.0, 1.0)


def _check_frame(frame: FrameConfig) -> None:
    """Rahmenbreite, Erhöhung und Teilarten des Rahmens."""
    _check_positive("Rahmenbreite", frame.width, allow_zero=True)
    _check_range("Rahmen-Erhöhung", frame.raise_plates, 0, MAX_FRAME_RAISE, "Plates")
    if frame.part_kinds is not None:
        _check_part_kinds(frame.part_kinds)


def _check_part_kinds(part_kinds: tuple[PartKind, ...]) -> None:
    """Mindestens eine Teilart, keine doppelt, keine Grundplatte als Mosaikteil."""
    _check_unique_non_empty("Teilarten", part_kinds)
    invalid = [kind.value for kind in part_kinds if kind not in MOSAIC_PART_KINDS]
    if invalid:
        valid = _join(kind.value for kind in MOSAIC_PART_KINDS)
        raise ConfigError(f"Ungültige Teilart {_join(invalid)}. Gültig sind: {valid}.")


def _check_unique_non_empty(label: str, values: tuple[str, ...]) -> None:
    """Prüft, dass eine Liste nicht leer ist und keine Duplikate enthält."""
    if not values:
        raise ConfigError(f"{label}: Es muss mindestens ein Wert angegeben werden.")
    # Ein set enthält jedes Element nur einmal; ist es kürzer als die Liste, gab es Duplikate
    if len(set(values)) != len(values):
        raise ConfigError(f"{label}: Jeder Wert darf nur einmal vorkommen ({_join(values)}).")


@dataclass(frozen=True, slots=True)
class OverrideInput:
    """Rohe Eingaben (Text und Zahlen), so wie sie von CLI, API oder interaktivem Modus kommen.

    Alle drei Oberflächen füllen nur dieses Objekt; ``parse_overrides`` übersetzt es
    an einer einzigen Stelle in Fachwerte (DRY). ``None`` = Standard aus ``CONFIG``.
    """

    # Größe und Unterbau
    substrate: str | None = None  # art, grundplatte, ohne
    plate_studs: int | None = None
    art_format: str | None = None  # z. B. "quadrat" (3 × 3 Platten), siehe layout.FORMATS
    plates: str | None = None  # BxH, z. B. "3x3"
    width_studs: int | None = None
    height_studs: int | None = None
    substrate_color_id: int | None = None
    # Rahmen
    frame_width: int | None = None
    frame_color_id: int | None = None
    frame_corner_color_id: int | None = None
    frame_part_kinds: str | None = None  # z. B. "tile"
    frame_raise_plates: int | None = None
    # Bild
    fit_mode: str | None = None  # zuschneiden, einpassen
    contrast: float | None = None
    saturation: float | None = None
    brightness: float | None = None
    color_filter: str | None = None  # ohne, graustufen, sepia, …
    filter_strength: float | None = None
    rotate: int | None = None  # 0, 90, 180, 270
    mirror: bool | None = None
    crop: str | None = None  # "x,y,b,h" in Prozent, z. B. "10,0,80,100"
    background: str | None = None  # behalten, entfernen, aufhellen, …
    background_color_id: int | None = None
    background_tolerance: float | None = None
    background_amount: float | None = None
    # Farben und Teile
    part_kinds: str | None = None  # z. B. "plate,tile"
    color_categories: str | None = None  # z. B. "solid,pearl"
    exotic_colors: bool = False
    max_colors: int | None = None  # 0 = keine Begrenzung
    color_ids: str | None = None  # eigene Palette als Farb-IDs, z. B. "0,15,71"
    # Stil und Relief
    style: str | None = None  # quadrat, formen, punkte, noppen
    relief: str | None = None  # ohne, motiv, hell, dunkel
    relief_levels: int | None = None
    # Verfahren und Ausgaben
    dithering_method: str | None = None
    strength: float | None = None
    color_distance: str | None = None  # cie76, ciede2000
    despeckle: bool | None = None  # einzelne Pixel entfernen (None = Standard)
    smoothing: str | None = None  # aus, leicht, mittel, stark
    tiling_strategy: str | None = None
    outputs: str | None = None  # z. B. "preview_png,ldraw"
    output_dir: Path | None = None
    # Name des Mosaiks (Titelblatt, Ordnername); kein Konfigurationswert, gilt nur für den Lauf
    title: str | None = None


# Formularfelder von ``POST /mosaike`` (auch CLI-Optionen und Presets) → Feld in OverrideInput.
# Eine Tabelle für alle Oberflächen: Presets speichern genau diese Namen.
FORM_FIELDS: Mapping[str, str] = {
    "unterbau": "substrate",
    "plattengroesse": "plate_studs",
    "format": "art_format",
    "platten": "plates",
    "breite": "width_studs",
    "hoehe": "height_studs",
    "unterbaufarbe": "substrate_color_id",
    "rahmen": "frame_width",
    "rahmenfarbe": "frame_color_id",
    "eckenfarbe": "frame_corner_color_id",
    "rahmen_teile": "frame_part_kinds",
    "rahmen_erhoehung": "frame_raise_plates",
    "modus": "fit_mode",
    "kontrast": "contrast",
    "saettigung": "saturation",
    "helligkeit": "brightness",
    "farbfilter": "color_filter",
    "filter_staerke": "filter_strength",
    "drehen": "rotate",
    "spiegeln": "mirror",
    "ausschnitt": "crop",
    "hintergrund": "background",
    "hintergrundfarbe": "background_color_id",
    "hintergrund_toleranz": "background_tolerance",
    "hintergrund_staerke": "background_amount",
    "teile": "part_kinds",
    "farbkategorien": "color_categories",
    "exotische_farben": "exotic_colors",
    "max_farben": "max_colors",
    "farb_ids": "color_ids",
    "stil": "style",
    "relief": "relief",
    "relief_hoehe": "relief_levels",
    "dithering": "dithering_method",
    "staerke": "strength",
    "farbabstand": "color_distance",
    "aufraeumen": "despeckle",
    "glaettung": "smoothing",
    "zerlegung": "tiling_strategy",
    "ausgaben": "outputs",
    "titel": "title",
}


def override_input(values: Mapping[str, object]) -> OverrideInput:
    """Formularfelder (deutsche Namen, ``None`` = nicht angegeben) → ``OverrideInput``.

    Ganze Zahlen gelten auch für Kommazahl-Felder; sonst muss der Typ stimmen.

    Raises:
        ConfigError: Bei unbekannten Feldern oder falschem Typ.
    """
    unknown = sorted(set(values) - set(FORM_FIELDS))
    if unknown:
        raise ConfigError(f"Unbekannte Felder: {_join(unknown)}.")
    hints = get_type_hints(OverrideInput)
    fields: dict[str, Any] = {}
    for name, value in values.items():
        if value is None:
            continue
        attribute = FORM_FIELDS[name]
        if not _fits(value, hints[attribute]):
            raise ConfigError(f"Ungültiger Wert für „{name}“: {value!r}.")
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and _expects(hints[attribute], float)
        ):
            value = float(value)  # 1 → 1.0 für Kommazahl-Felder
        fields[attribute] = value
    return OverrideInput(**fields)


def form_values(raw: OverrideInput) -> dict[str, object]:
    """Gegenstück zu ``override_input``: nur angegebene Felder, mit deutschen Namen."""
    values = {name: getattr(raw, attribute) for name, attribute in FORM_FIELDS.items()}
    # exotische_farben ist ein Schalter: False heißt „nicht angegeben“
    return {
        name: value for name, value in values.items() if value is not None and value is not False
    }


def _expects(hint: object, kind: type) -> bool:
    """True, wenn der Typ-Hinweis (z. B. ``float | None``) ``kind`` erlaubt."""
    return kind in getattr(hint, "__args__", (hint,))


def _fits(value: object, hint: object) -> bool:
    """Prüft einen Wert gegen ``str``, ``int``, ``float`` oder ``bool`` (bool ist kein int)."""
    if isinstance(value, bool):
        return _expects(hint, bool)
    if isinstance(value, int):
        return _expects(hint, int) or _expects(hint, float)
    if isinstance(value, float):
        return _expects(hint, float)
    return isinstance(value, str) and _expects(hint, str)


def run_title(raw: OverrideInput, fallback: str) -> str:
    """Name des Mosaiks: eingegeben oder ``fallback`` (meist der Name der Bilddatei)."""
    title = (raw.title or "").strip()
    return title or fallback


def parse_overrides(raw: OverrideInput) -> MosaicOverrides:
    """Übersetzt rohe Eingaben in ``MosaicOverrides`` (Namen → Enums, Listen → Tupel).

    Raises:
        ConfigError: Bei unbekannten Namen oder falschem Format.
    """
    # Bedingter Ausdruck: Ohne Angabe bleibt das Tupel (None, None) → Standardwerte
    wide, high = parse_plates(raw.plates) if raw.plates else (None, None)
    if raw.art_format:
        if raw.plates or raw.width_studs is not None or raw.height_studs is not None:
            raise ConfigError(
                "Bitte entweder ein Format oder Platten bzw. eine eigene Größe angeben."
            )
        art_format = find_format(raw.art_format)
        wide, high = art_format.plates_wide, art_format.plates_high
    return MosaicOverrides(
        substrate=_parse_label(SUBSTRATE_LABELS, raw.substrate, "Unterbau"),
        plate_studs=raw.plate_studs,
        plates_wide=wide,
        plates_high=high,
        width_studs=raw.width_studs,
        height_studs=raw.height_studs,
        substrate_color_id=raw.substrate_color_id,
        frame_width=raw.frame_width,
        frame_color_id=raw.frame_color_id,
        frame_corner_color_id=raw.frame_corner_color_id,
        frame_part_kinds=parse_part_kinds(raw.frame_part_kinds) if raw.frame_part_kinds else None,
        frame_raise_plates=raw.frame_raise_plates,
        fit_mode=_parse_label(FIT_MODE_LABELS, raw.fit_mode, "Modus"),
        contrast=raw.contrast,
        saturation=raw.saturation,
        brightness=raw.brightness,
        color_filter=_parse_label(COLOR_FILTER_LABELS, raw.color_filter, "Farbfilter"),
        filter_strength=raw.filter_strength,
        rotate=raw.rotate,
        mirror=raw.mirror,
        crop=parse_crop(raw.crop) if raw.crop else None,
        background=_parse_label(BACKGROUND_LABELS, raw.background, "Hintergrund-Art"),
        background_color_id=raw.background_color_id,
        background_tolerance=raw.background_tolerance,
        background_amount=raw.background_amount,
        part_kinds=parse_part_kinds(raw.part_kinds) if raw.part_kinds else None,
        color_categories=combine_categories(raw.color_categories, exotic=raw.exotic_colors),
        max_colors=raw.max_colors,
        only_color_ids=parse_color_ids(raw.color_ids) if raw.color_ids else None,
        style=raw.style.strip().lower() if raw.style else None,
        relief=_parse_label(RELIEF_LABELS, raw.relief, "Relief-Art"),
        relief_levels=raw.relief_levels,
        dithering_method=raw.dithering_method,
        strength=raw.strength,
        color_distance=_parse_label(COLOR_DISTANCE_LABELS, raw.color_distance, "Farbabstand"),
        despeckle=raw.despeckle,
        smoothing=_parse_label(SMOOTHING_LABELS, raw.smoothing, "Glättung"),
        tiling_strategy=raw.tiling_strategy,
        outputs=parse_names(raw.outputs) if raw.outputs else None,
        output_dir=raw.output_dir,
    )


def _parse_label[T](labels: Mapping[str, T], text: str | None, label: str) -> T | None:
    """Deutsche Bezeichnung → Wert; ``None`` oder leer bleibt ``None`` (= Standard)."""
    return _lookup(labels, _key(text), label) if text else None


def combine_categories(text: str | None, *, exotic: bool) -> tuple[ColorCategory, ...] | None:
    """Farbkategorien aus Text und dem Schalter „exotische Farben“ (vereinigt).

    Ohne Text gilt SOLID als Basis; ``exotic`` ergänzt alle exotischen Kategorien.
    ``None`` heißt: Wert aus der Konfiguration übernehmen.
    """
    if text is None and not exotic:
        return None
    chosen = list(parse_color_categories(text)) if text else [ColorCategory.SOLID]
    if exotic:
        chosen += [category for category in EXOTIC_CATEGORIES if category not in chosen]
    return tuple(chosen)


def config_to_dict(config: AppConfig) -> dict[str, object]:
    """Wandelt die Konfiguration in JSON-fähige Werte um (für ``ergebnis.json``).

    ``asdict`` macht aus verschachtelten Dataclasses verschachtelte dicts; Pfade,
    Enums und Tupel wandelt ``_to_json`` anschließend rekursiv um.
    """
    result = _to_json(asdict(config))
    assert isinstance(result, dict)  # asdict liefert immer ein dict
    return result


def _to_json(value: object) -> object:
    """Rekursive Umwandlung: Path → str, Enum → Wert, Tupel/Listen → Listen."""
    # match/case (Structural Pattern Matching): wählt den Zweig nach dem Typ des Werts
    match value:
        case dict():
            return {str(key): _to_json(item) for key, item in value.items()}
        case tuple() | list():
            return [_to_json(item) for item in value]
        case Path():
            return str(value)
        case Enum():
            return value.value
        case _:
            return value


# --- Eingaben aus Text parsen (gemeinsam für CLI und API) -------------------------


def parse_names(text: str) -> tuple[str, ...]:
    """Zerlegt eine kommagetrennte Liste, z. B. ``"plate, tile"`` → ``("plate", "tile")``."""
    # strip() entfernt Leerzeichen am Rand, "if name" filtert leere Einträge ("a,,b")
    return tuple(name.strip().lower() for name in text.split(",") if name.strip())


def parse_part_kinds(text: str) -> tuple[PartKind, ...]:
    """Wandelt ``"plate,tile"`` in Teilarten um.

    Raises:
        ConfigError: Bei unbekannten Namen, mit Liste der gültigen Namen.
    """
    valid = {kind.value: kind for kind in MOSAIC_PART_KINDS}
    return tuple(_lookup(valid, name, "Teilart") for name in parse_names(text))


def parse_plates(text: str) -> tuple[int, int]:
    """Wandelt ``"2x3"`` (Breite × Höhe in Grundplatten) in ``(2, 3)`` um.

    Raises:
        ConfigError: Wenn das Format nicht „BxH“ mit ganzen Zahlen ist.
    """
    # partition teilt am ersten Vorkommen: "2x3" → ("2", "x", "3")
    wide, separator, high = text.strip().lower().replace("×", "x").partition("x")
    if not separator or not wide.strip().isdigit() or not high.strip().isdigit():
        raise ConfigError(f"Grundplatten bitte als „BxH“ angeben, z. B. „2x2“ (angegeben: {text}).")
    return int(wide), int(high)


def parse_color_categories(text: str) -> tuple[ColorCategory, ...]:
    """Wandelt ``"solid,metallic"`` in Farbkategorien um.

    Raises:
        ConfigError: Bei unbekannten Namen, mit Liste der gültigen Namen.
    """
    valid = {category.value: category for category in ColorCategory}
    return tuple(_lookup(valid, name, "Farbkategorie") for name in parse_names(text))


def parse_crop(text: str) -> tuple[float, float, float, float]:
    """Wandelt ``"10,0,80,100"`` (x, y, Breite, Höhe in Prozent) in ein Tupel um.

    Raises:
        ConfigError: Wenn es nicht genau vier Zahlen sind.
    """
    try:
        values = tuple(float(part.replace("%", "")) for part in text.split(","))
    except ValueError:
        values = ()
    if len(values) != CROP_VALUES:
        raise ConfigError(
            f"Ausschnitt bitte als „x,y,Breite,Höhe“ in Prozent angeben, z. B. „10,0,80,100“ "
            f"(angegeben: {text})."
        )
    x, y, width, height = values
    return x, y, width, height


def parse_color_ids(text: str) -> tuple[int, ...]:
    """Wandelt ``"0, 15, 71"`` in Farb-IDs um.

    Raises:
        ConfigError: Wenn ein Eintrag keine ganze Zahl ist.
    """
    names = parse_names(text)
    if not all(name.lstrip("-").isdigit() for name in names):
        raise ConfigError(f"Farb-IDs bitte als Zahlen mit Komma angeben, z. B. „0,15,71“ ({text}).")
    return tuple(int(name) for name in names)


def parse_fit_mode(text: str) -> FitMode:
    """Wandelt ``"zuschneiden"`` bzw. ``"einpassen"`` in einen ``FitMode`` um."""
    return _lookup(FIT_MODE_LABELS, _key(text), "Modus")


def label_of[T](labels: Mapping[str, T], value: T) -> str:
    """Deutsche Bezeichnung zu einem Wert (Umkehrung der ``*_LABELS``-Tabellen)."""
    # next(...) liefert das erste Element des Generators; str(value) ist der Notnagel
    return next((label for label, item in labels.items() if item == value), str(value))


def _key(text: str) -> str:
    """Normalisiert eine Texteingabe für die Suche in den Bezeichnungs-Tabellen."""
    return text.strip().lower()


def _lookup[T](choices: Mapping[str, T], name: str, label: str) -> T:
    """Sucht einen Namen; unbekannte Namen ergeben eine Meldung mit allen gültigen Werten.

    ``[T]`` hinter dem Funktionsnamen ist ein Typparameter (PEP 695): Die Funktion
    gibt denselben Typ zurück, den die Werte des Mappings haben.
    """
    try:
        return choices[name]
    except KeyError:
        # "from None" unterdrückt den KeyError als Ursache, die Meldung reicht völlig
        raise ConfigError(
            f"Unbekannte {label} „{name}“. Gültig sind: {_join(sorted(choices))}."
        ) from None


def _join(values: Iterable[str]) -> str:
    """Verbindet Werte zu ``"a, b, c"``."""
    return ", ".join(values)
