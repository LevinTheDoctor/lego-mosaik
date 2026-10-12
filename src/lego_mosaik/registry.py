"""Registries: Name aus der Konfiguration → Fabrikfunktion der Implementierung.

Registry Pattern + Factory: Jede Registry ist ein ganz normales ``dict``. Der
Schlüssel ist der Name aus der Konfiguration (z. B. ``"floyd_steinberg"``), der
Wert eine Fabrikfunktion, die aus der ``AppConfig`` eine fertige Instanz baut.

Open/Closed-Prinzip: Ein neues Verfahren = eine neue Klasse + eine Zeile hier.
Pipeline, CLI und API bleiben unverändert.

Bewusst explizite Dicts statt Selbstregistrierung per Import-Seiteneffekt: Mit
``lazy import`` würden solche Seiteneffekte erst beim ersten Zugriff passieren
und die Registry wäre beim Start womöglich leer.
"""

from collections.abc import Callable, Mapping

from lego_mosaik.config import AppConfig
from lego_mosaik.domain.errors import ConfigError
lazy from lego_mosaik.dithering.error_diffusion import (
    ATKINSON,
    FLOYD_STEINBERG,
    SIERRA_LITE,
    DiffusionKernel,
    ErrorDiffusionDitherer,
)
lazy from lego_mosaik.dithering.nearest import NearestColorDitherer
lazy from lego_mosaik.dithering.ordered import OrderedDitherer
lazy from lego_mosaik.dithering.protocol import Ditherer
lazy from lego_mosaik.imaging.color_space import LabColorSpace
lazy from lego_mosaik.outputs.protocol import OutputWriter
lazy from lego_mosaik.tiling.protocol import TilingStrategy

# Typ-Aliase für die Fabrikfunktionen: Callable[[Argumenttypen], Rückgabetyp]
type DithererFactory = Callable[[AppConfig], Ditherer]
type TilingFactory = Callable[[AppConfig], TilingStrategy]
type OutputWriterFactory = Callable[[AppConfig], OutputWriter]


def _color_space(config: AppConfig) -> LabColorSpace:
    """Farbsuche mit dem konfigurierten Farbabstand (für alle Dithering-Verfahren)."""
    return LabColorSpace(config.dithering.color_distance)


def _error_diffusion(config: AppConfig, kernel: DiffusionKernel) -> ErrorDiffusionDitherer:
    """Fehlerdiffusion mit dem gegebenen Kernel und den Werten aus der Konfiguration."""
    return ErrorDiffusionDitherer(
        kernel,
        _color_space(config),
        strength=config.dithering.strength,
        serpentine=config.dithering.serpentine,
    )


# Die lambdas werden erst beim Aufruf ausgewertet; dadurch bleiben die lazy
# importierten Module (und NumPy/scikit-image) bis zum ersten Mosaik ungeladen.
DITHERERS: dict[str, DithererFactory] = {
    "nearest": lambda config: NearestColorDitherer(_color_space(config)),
    "floyd_steinberg": lambda config: _error_diffusion(config, FLOYD_STEINBERG),
    "atkinson": lambda config: _error_diffusion(config, ATKINSON),
    "sierra_lite": lambda config: _error_diffusion(config, SIERRA_LITE),
    "ordered": lambda config: OrderedDitherer(
        _color_space(config),
        size=config.dithering.bayer_size,
        spread=config.dithering.spread,
    ),
}

TILING_STRATEGIES: dict[str, TilingFactory] = {}

OUTPUT_WRITERS: dict[str, OutputWriterFactory] = {}


def resolve[T](registry: Mapping[str, T], name: str, label: str) -> T:
    """Holt den Eintrag zu ``name`` aus einer Registry.

    Args:
        registry: Eine der Registries oben.
        name: Gesuchter Name.
        label: Deutsche Bezeichnung für die Fehlermeldung, z. B. "Dithering-Verfahren".

    Raises:
        ConfigError: Wenn der Name unbekannt ist. Die Meldung listet alle gültigen Namen.
    """
    try:
        return registry[name]
    except KeyError:
        valid = ", ".join(sorted(registry)) or "(keine)"
        raise ConfigError(f"Unbekanntes {label} „{name}“. Gültig sind: {valid}.") from None


def validate_registry_names(config: AppConfig) -> None:
    """Prüft, dass alle Namen aus der Konfiguration registriert sind.

    Raises:
        ConfigError: Beim ersten unbekannten Namen.
    """
    resolve(DITHERERS, config.dithering.method, "Dithering-Verfahren")
    resolve(TILING_STRATEGIES, config.tiling.strategy, "Zerlegungsverfahren")
    for output_name in config.outputs:
        resolve(OUTPUT_WRITERS, output_name, "Ausgabeformat")
