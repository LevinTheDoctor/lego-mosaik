"""Konfiguration als unveränderliche Dataclasses plus Overrides und Validierung.

Die konkreten Standardwerte stehen nicht hier, sondern in ``main.py`` (Composition
Root). Dieses Modul beschreibt nur die *Form* der Konfiguration und enthält die
einzige Stelle, an der Eingaben aus CLI und API geprüft und angewendet werden.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.models import ColorDistance, FitMode, PartKind, Resampling

# Grenzen für die Mosaikgröße in Noppen
MIN_STUDS = 8
MAX_STUDS = 256
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
class MosaicConfig:
    """Größe des Mosaiks und Bildaufbereitung."""

    width_studs: int
    height_studs: int
    fit_mode: FitMode = FitMode.CROP
    pad_color_id: int = 15  # Rebrickable-ID 15 = White
    resampling: Resampling = Resampling.BOX
    contrast: float = 1.0  # 1.0 = unverändert
    saturation: float = 1.0  # 1.0 = unverändert


@dataclass(frozen=True, slots=True)
class PaletteConfig:
    """Welche Teilarten und Farben erlaubt sind."""

    part_kinds: tuple[PartKind, ...]
    exclude_transparent: bool = True
    exclude_color_ids: tuple[int, ...] = ()
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
    include_baseplate: bool = False


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

    width_studs: int | None = None
    height_studs: int | None = None
    fit_mode: FitMode | None = None
    part_kinds: tuple[PartKind, ...] | None = None
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
    mosaic, dithering = config.mosaic, config.dithering
    run_config = replace(
        config,
        mosaic=replace(
            mosaic,
            width_studs=_or_default(overrides.width_studs, mosaic.width_studs),
            height_studs=_or_default(overrides.height_studs, mosaic.height_studs),
            fit_mode=_or_default(overrides.fit_mode, mosaic.fit_mode),
        ),
        palette=replace(
            config.palette,
            part_kinds=_or_default(overrides.part_kinds, config.palette.part_kinds),
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
    _check_range("Breite", config.mosaic.width_studs, MIN_STUDS, MAX_STUDS, "Noppen")
    _check_range("Höhe", config.mosaic.height_studs, MIN_STUDS, MAX_STUDS, "Noppen")
    _check_range("Dithering-Stärke", config.dithering.strength, 0.0, 1.0)
    _check_positive("Kontrast", config.mosaic.contrast)
    _check_positive("Sättigung", config.mosaic.saturation)
    _check_part_kinds(config.palette.part_kinds)
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


def _check_part_kinds(part_kinds: tuple[PartKind, ...]) -> None:
    """Mindestens eine Teilart, keine doppelt."""
    _check_unique_non_empty("Teilarten", part_kinds)


def _check_unique_non_empty(label: str, values: tuple[str, ...]) -> None:
    """Prüft, dass eine Liste nicht leer ist und keine Duplikate enthält."""
    if not values:
        raise ConfigError(f"{label}: Es muss mindestens ein Wert angegeben werden.")
    # Ein set enthält jedes Element nur einmal; ist es kürzer als die Liste, gab es Duplikate
    if len(set(values)) != len(values):
        raise ConfigError(f"{label}: Jeder Wert darf nur einmal vorkommen ({_join(values)}).")


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
    valid = {kind.value: kind for kind in PartKind}
    return tuple(_lookup(valid, name, "Teilart") for name in parse_names(text))


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
