"""Konfiguration als unveränderliche Dataclasses plus Overrides und Validierung.

Die konkreten Standardwerte stehen nicht hier, sondern in ``main.py`` (Composition
Root). Dieses Modul beschreibt nur die *Form* der Konfiguration und enthält die
einzige Stelle, an der Eingaben aus CLI und API geprüft und angewendet werden.
"""

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Protocol

from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.layout import MAX_STUDS, MIN_STUDS, check_size, find_format, snap_to_plates
from lego_mosaik.domain.models import (
    MOSAIC_PART_KINDS,
    ColorCategory,
    ColorDistance,
    FitMode,
    PartKind,
    PartShape,
    ReliefMode,
    Resampling,
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
MAX_SUBSAMPLE = 12  # Teilpixel pro Noppe und Richtung bei der Formwahl


# Deutsche Bezeichnungen für CLI, API und interaktiven Modus → interne Enum-Werte
FIT_MODE_LABELS: Mapping[str, FitMode] = {"zuschneiden": FitMode.CROP, "einpassen": FitMode.PAD}
COLOR_DISTANCE_LABELS: Mapping[str, ColorDistance] = {
    "cie76": ColorDistance.CIE76,
    "ciede2000": ColorDistance.CIEDE2000,
}
SUBSTRATE_LABELS: Mapping[str, Substrate] = {
    "art": Substrate.ART_PANEL,
    "grundplatte": Substrate.BASEPLATE,
    "ohne": Substrate.NONE,
}
RELIEF_LABELS: Mapping[str, ReliefMode] = {
    "ohne": ReliefMode.NONE,
    "motiv": ReliefMode.SUBJECT,
    "hell": ReliefMode.BRIGHT,
    "dunkel": ReliefMode.DARK,
}
# Standardfarbe des Unterbaus, wenn keine angegeben ist (Rebrickable-IDs)
DEFAULT_SUBSTRATE_COLOR_IDS: Mapping[Substrate, int] = {
    Substrate.BASEPLATE: 71,  # Light Bluish Gray
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
    plate_studs: int = 32  # Grundplatte 16, 32 oder 48; Art-Paneel 16
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
    """Bildaufbereitung: Einpassen, Randfarbe, Skalierung, Kontrast und Sättigung."""

    fit_mode: FitMode = FitMode.CROP
    pad_color_id: int = 15  # Rebrickable-ID 15 = White
    resampling: Resampling = Resampling.BOX
    contrast: float = 1.0  # 1.0 = unverändert
    saturation: float = 1.0  # 1.0 = unverändert


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
    max_colors: int | None = None  # None = keine Begrenzung


@dataclass(frozen=True, slots=True)
class DitheringConfig:
    """Auswahl und Parameter des Dithering-Verfahrens."""

    method: str  # Schlüssel aus der Registry
    strength: float = 1.0  # 0.0 = kein Fehlertransport, 1.0 = voll
    color_distance: ColorDistance = ColorDistance.CIEDE2000
    serpentine: bool = True  # Zeilen abwechselnd links/rechts abarbeiten
    bayer_size: int = 4  # nur für "ordered": 2, 4 oder 8
    spread: float = 32.0  # nur für "ordered": Stärke des Schwellwert-Offsets in RGB-Stufen


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
    - ``formen``: an Kanten Viertelkreise, Halbkreise, Dreiecke und Cut-Corner-Wedges,
      wo sie die Kontur genauer treffen (Bild wird dafür feiner ausgewertet)
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
    """Aufbau der Bauanleitung (Seitengröße entspricht A4 bei 150 dpi)."""

    section_studs: int = 16  # Abschnittsgröße in Noppen (quadratisch)
    page_width_px: int = 1240
    page_height_px: int = 1754
    margin_px: int = 60
    max_stud_px: int = 48  # größte Noppe auf der Abschnittskarte
    # Kamera der 3D-Ansichten: gerade von vorne, steil von oben (wie LEGO Art);
    # Zeilen und Spalten bleiben abzählbar. Drehung > 0 zeigt zusätzlich Seitenflächen.
    view_elevation_deg: float = 60.0
    view_azimuth_deg: float = 0.0
    supersample: int = 2  # intern größer rendern und verkleinern (glatte Kanten)
    step_render_height_px: int = 820  # Höhe einer Schrittansicht in Pixeln
    # TrueType-Schriften mit Umlauten; die erste vorhandene wird genutzt.
    # Pillows eingebaute Schrift kann weder ä/ö/ü noch × und –.
    font_paths: tuple[Path, ...] = (
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),  # macOS
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),  # Debian/Ubuntu
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),  # Arch
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),  # Windows
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
    # Farben und Teile
    part_kinds: tuple[PartKind, ...] | None = None
    color_categories: tuple[ColorCategory, ...] | None = None
    max_colors: int | None = None  # 0 = keine Begrenzung
    # Stil und Relief
    style: str | None = None
    relief: ReliefMode | None = None
    relief_levels: int | None = None
    # Verfahren und Ausgaben
    dithering_method: str | None = None
    strength: float | None = None
    color_distance: ColorDistance | None = None
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
            config.mosaic, fit_mode=o.fit_mode, contrast=o.contrast, saturation=o.saturation
        ),
        palette=_merged(
            config.palette,
            part_kinds=o.part_kinds,
            color_categories=o.color_categories,
            max_colors=o.max_colors,
        ),
        dithering=_merged(
            config.dithering,
            method=o.dithering_method,
            strength=o.strength,
            color_distance=o.color_distance,
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
    _check_positive("Kontrast", config.mosaic.contrast)
    _check_positive("Sättigung", config.mosaic.saturation)
    _check_part_kinds(config.palette.part_kinds)
    _check_unique_non_empty("Farbkategorien", config.palette.color_categories)
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
    _check_range("Port", config.server.port, MIN_PORT, MAX_PORT)
    _check_range("Upload-Grenze", config.server.max_upload_mb, 1, 1024, "MB")


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
    """Formteile brauchen einen Unterbau (er ist in den Lücken zu sehen) und flache Teile."""
    shapes = config.shapes
    _check_range("Teilpixel der Formwahl", shapes.subsample, 2, MAX_SUBSAMPLE)
    _check_range("Mindestgewinn der Formwahl", shapes.min_gain, 0.0, 1.0)
    _check_positive("Mindestverbesserung der Formwahl", shapes.min_improvement, allow_zero=True)
    _check_unique_non_empty("Formteile", shapes.shapes)
    if PartShape.SQUARE in shapes.shapes:
        raise ConfigError("Formteile: „square“ ist kein Formteil.")
    if shapes.style == PLAIN_STYLE:
        return
    if config.layout.substrate is Substrate.NONE:
        raise ConfigError(
            f"Der Stil „{shapes.style}“ braucht einen Unterbau (art oder grundplatte): "
            "In den Lücken der runden und schrägen Teile ist er zu sehen."
        )
    if PartKind.BRICK in config.palette.part_kinds:
        raise ConfigError(
            f"Der Stil „{shapes.style}“ geht nur mit Plates und Tiles, nicht mit Bricks."
        )


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
    # Farben und Teile
    part_kinds: str | None = None  # z. B. "plate,tile"
    color_categories: str | None = None  # z. B. "solid,pearl"
    exotic_colors: bool = False
    max_colors: int | None = None  # 0 = keine Begrenzung
    # Stil und Relief
    style: str | None = None  # quadrat, formen, punkte, noppen
    relief: str | None = None  # ohne, motiv, hell, dunkel
    relief_levels: int | None = None
    # Verfahren und Ausgaben
    dithering_method: str | None = None
    strength: float | None = None
    color_distance: str | None = None  # cie76, ciede2000
    tiling_strategy: str | None = None
    outputs: str | None = None  # z. B. "preview_png,ldraw"
    output_dir: Path | None = None


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
        part_kinds=parse_part_kinds(raw.part_kinds) if raw.part_kinds else None,
        color_categories=combine_categories(raw.color_categories, exotic=raw.exotic_colors),
        max_colors=raw.max_colors,
        style=raw.style.strip().lower() if raw.style else None,
        relief=_parse_label(RELIEF_LABELS, raw.relief, "Relief-Art"),
        relief_levels=raw.relief_levels,
        dithering_method=raw.dithering_method,
        strength=raw.strength,
        color_distance=_parse_label(COLOR_DISTANCE_LABELS, raw.color_distance, "Farbabstand"),
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
