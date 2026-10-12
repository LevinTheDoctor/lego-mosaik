"""Konfiguration als unveränderliche Dataclasses plus Overrides und Validierung.

Die konkreten Standardwerte stehen nicht hier, sondern in ``main.py`` (Composition
Root). Dieses Modul beschreibt nur die *Form* der Konfiguration und enthält die
einzige Stelle, an der Eingaben aus CLI und API geprüft und angewendet werden.
"""

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from pathlib import Path

from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.models import (
    MOSAIC_PART_KINDS,
    ColorCategory,
    ColorDistance,
    FitMode,
    PartKind,
    Resampling,
)

# Grenzen für die Mosaikgröße in Noppen
MAX_STUDS = 256  # Gesamtgröße pro Seite
MIN_IMAGE_STUDS = 4  # Bildfläche innerhalb des Rahmens pro Seite
MAX_PLATES = 16  # Grundplatten pro Richtung
# Erlaubte Größen der Bayer-Matrix beim Ordered Dithering
BAYER_SIZES = (2, 4, 8)
# Gültiger Bereich für TCP-Ports
MIN_PORT = 1
MAX_PORT = 65535


# Deutsche Bezeichnungen für die CLI und API → interne Enum-Werte
FIT_MODE_LABELS: Mapping[str, FitMode] = {"zuschneiden": FitMode.CROP, "einpassen": FitMode.PAD}
COLOR_DISTANCE_LABELS: Mapping[str, ColorDistance] = {
    "cie76": ColorDistance.CIE76,
    "ciede2000": ColorDistance.CIEDE2000,
}


@dataclass(frozen=True, slots=True)
class LayoutConfig:
    """Größe in Grundplatten, Grundplatte und Rahmen.

    Gesamtgröße = Grundplatten × Plattengröße. Die Bildfläche ist die Gesamtgröße
    minus den Rahmen auf beiden Seiten; der Rahmen liegt auf dem äußeren Ring.
    """

    baseplate_studs: int = 32  # 16, 32 oder 48 (muss im Katalog als Grundplatte stehen)
    plates_wide: int = 1  # Grundplatten nebeneinander
    plates_high: int = 1  # Grundplatten untereinander
    include_baseplate: bool = True  # Grundplatten in LDraw-Datei und Teilelisten aufnehmen
    baseplate_color_id: int = 71  # Rebrickable-ID 71 = Light Bluish Gray
    frame_width: int = 1  # Rahmenbreite in Noppen (0 = kein Rahmen)
    frame_color_id: int = 0  # Rebrickable-ID 0 = Black


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
    """Aufbau der Bauanleitung."""

    section_studs: int = 16  # Abschnittsgröße in Noppen (quadratisch)
    stud_px: int = 32  # Kantenlänge einer Noppe auf den Abschnittsseiten


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
    preview: PreviewConfig = field(default_factory=PreviewConfig)
    ldraw: LdrawConfig = field(default_factory=LdrawConfig)
    instructions: InstructionsConfig = field(default_factory=InstructionsConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


@dataclass(frozen=True, slots=True)
class MosaicOverrides:
    """Werte, die CLI oder API für einen einzelnen Lauf überschreiben.

    ``None`` heißt: Wert aus der Konfiguration übernehmen. CLI und API übersetzen
    ihre Eingaben in dieses eine Objekt (DRY), geprüft wird nur in ``apply_overrides``.
    """

    baseplate_studs: int | None = None
    plates_wide: int | None = None
    plates_high: int | None = None
    include_baseplate: bool | None = None
    frame_width: int | None = None
    frame_color_id: int | None = None
    fit_mode: FitMode | None = None
    part_kinds: tuple[PartKind, ...] | None = None
    color_categories: tuple[ColorCategory, ...] | None = None
    dithering_method: str | None = None
    strength: float | None = None
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
    layout, dithering = config.layout, config.dithering
    run_config = replace(
        config,
        layout=replace(
            layout,
            baseplate_studs=_or_default(overrides.baseplate_studs, layout.baseplate_studs),
            plates_wide=_or_default(overrides.plates_wide, layout.plates_wide),
            plates_high=_or_default(overrides.plates_high, layout.plates_high),
            include_baseplate=_or_default(overrides.include_baseplate, layout.include_baseplate),
            frame_width=_or_default(overrides.frame_width, layout.frame_width),
            frame_color_id=_or_default(overrides.frame_color_id, layout.frame_color_id),
        ),
        mosaic=replace(
            config.mosaic, fit_mode=_or_default(overrides.fit_mode, config.mosaic.fit_mode)
        ),
        palette=replace(
            config.palette,
            part_kinds=_or_default(overrides.part_kinds, config.palette.part_kinds),
            color_categories=_or_default(
                overrides.color_categories, config.palette.color_categories
            ),
        ),
        dithering=replace(
            dithering,
            method=_or_default(overrides.dithering_method, dithering.method),
            strength=_or_default(overrides.strength, dithering.strength),
        ),
        tiling=replace(
            config.tiling,
            strategy=_or_default(overrides.tiling_strategy, config.tiling.strategy),
        ),
        outputs=_or_default(overrides.outputs, config.outputs),
        output_dir=_or_default(overrides.output_dir, config.output_dir),
    )
    validate_config(run_config)
    return run_config


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
    _check_layout(config.layout)
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


def _check_layout(layout: LayoutConfig) -> None:
    """Grundplatten-Anzahl, Gesamtgröße und verbleibende Bildfläche."""
    _check_range("Grundplatten nebeneinander", layout.plates_wide, 1, MAX_PLATES)
    _check_range("Grundplatten untereinander", layout.plates_high, 1, MAX_PLATES)
    _check_range("Grundplattengröße", layout.baseplate_studs, 1, MAX_STUDS, "Noppen")
    _check_positive("Rahmenbreite", layout.frame_width, allow_zero=True)
    longest = layout.baseplate_studs * max(layout.plates_wide, layout.plates_high)
    if longest > MAX_STUDS:
        raise ConfigError(
            f"Das Mosaik darf höchstens {MAX_STUDS} Noppen pro Seite haben "
            f"(angegeben: {longest} Noppen)."
        )
    shortest = layout.baseplate_studs * min(layout.plates_wide, layout.plates_high)
    if shortest - 2 * layout.frame_width < MIN_IMAGE_STUDS:
        raise ConfigError(
            f"Der Rahmen ist zu breit: Innen müssen mindestens {MIN_IMAGE_STUDS} Noppen "
            "für das Bild bleiben."
        )


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
    return _lookup(FIT_MODE_LABELS, text.strip().lower(), "Modus")


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
