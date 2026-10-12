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
lazy from lego_mosaik.dithering.protocol import Ditherer
lazy from lego_mosaik.outputs.protocol import OutputWriter
lazy from lego_mosaik.tiling.protocol import TilingStrategy

# Typ-Aliase für die Fabrikfunktionen: Callable[[Argumenttypen], Rückgabetyp]
type DithererFactory = Callable[[AppConfig], Ditherer]
type TilingFactory = Callable[[AppConfig], TilingStrategy]
type OutputWriterFactory = Callable[[AppConfig], OutputWriter]

DITHERERS: dict[str, DithererFactory] = {}

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
