"""Bibliothek aus Paletten und Presets: löst Preset- und Palettennamen in Eingaben auf."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from lego_mosaik.config import (
    FORM_FIELDS,
    MosaicOverrides,
    OverrideInput,
    StorageConfig,
    override_input,
    parse_overrides,
)
from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.library import Preset, SettingValue
from lego_mosaik.storage.palettes import PaletteStore
from lego_mosaik.storage.presets import PresetStore

PALETTE_FIELD = "palette"  # Name einer eigenen Palette (statt farb_ids)
COLOR_IDS_FIELD = "farb_ids"
# Felder, die nicht in ein Preset gehören: der Name gilt nur für ein Bild
NOT_IN_PRESETS = frozenset({"titel"})

type OverrideCheck = Callable[[MosaicOverrides], object]


@dataclass(frozen=True, slots=True)
class Resolved:
    """Eingaben eines Laufs nach Auflösen von Preset und Palette.

    Attributes:
        inputs: Die rohen Eingaben (wie von CLI oder API).
        values: Dieselben Werte mit Formularnamen (Palette bleibt als Name stehen).
        preset: Das benutzte Preset, falls eines angegeben war.
    """

    inputs: OverrideInput
    values: dict[str, SettingValue]
    preset: Preset | None


class Library:
    """Eigene Paletten und Presets für CLI, interaktiven Modus und API.

    Args:
        palettes: Speicher der Paletten.
        presets: Speicher der Presets.
    """

    def __init__(self, palettes: PaletteStore, presets: PresetStore) -> None:
        self.palettes = palettes
        self.presets = presets

    @classmethod
    def open(cls, storage: StorageConfig) -> Library:
        """Bibliothek mit den Dateien aus der Konfiguration."""
        return cls(PaletteStore(storage.palettes_file), PresetStore(storage.presets_file))

    def resolve(
        self,
        values: Mapping[str, object],
        *,
        preset: str | None = None,
        palette: str | None = None,
    ) -> Resolved:
        """Formularwerte plus Preset (Grundlage) und Palette (Name → Farb-IDs).

        Angegebene Werte gehen vor dem Preset. Eine angegebene Palette oder ``farb_ids``
        ersetzt die Palette des Presets.

        Raises:
            NotFoundError: Bei unbekanntem Preset oder unbekannter Palette.
            ConfigError: Bei unbekannten Feldern, falschen Typen oder Palette und Farb-IDs zugleich.
        """
        explicit = {name: value for name, value in values.items() if value is not None}
        if palette and COLOR_IDS_FIELD in explicit:
            raise ConfigError("Bitte entweder eine Palette oder Farb-IDs angeben, nicht beides.")
        chosen = self.presets.get(preset) if preset else None
        merged: dict[str, object] = dict(chosen.settings) if chosen else {}
        if palette or COLOR_IDS_FIELD in explicit:
            merged.pop(PALETTE_FIELD, None)
            merged.pop(COLOR_IDS_FIELD, None)
        merged |= explicit
        if palette:
            merged[PALETTE_FIELD] = palette
        form = dict(merged)
        name = form.pop(PALETTE_FIELD, None)
        if name is not None:
            ids = self.palettes.get(str(name)).color_ids
            form[COLOR_IDS_FIELD] = ",".join(map(str, ids))
        return Resolved(override_input(form), _setting_values(merged), chosen)

    def preset_settings(
        self, values: Mapping[str, object], check: OverrideCheck
    ) -> dict[str, SettingValue]:
        """Prüft die Einstellungen eines Presets und gibt sie bereinigt zurück.

        ``check`` prüft die fertigen Overrides (z. B. ``MosaicPipeline.check``), damit ein
        Preset nur gespeichert wird, wenn es auch funktioniert.

        Raises:
            ConfigError: Bei unbekannten oder unpassenden Feldern bzw. ungültigen Werten.
            NotFoundError: Bei einer unbekannten Palette.
        """
        excluded = sorted(NOT_IN_PRESETS & set(values))
        if excluded:
            raise ConfigError(f"Diese Felder gehören nicht in ein Preset: {', '.join(excluded)}.")
        allowed = set(FORM_FIELDS) | {PALETTE_FIELD}
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ConfigError(f"Unbekannte Felder im Preset: {', '.join(unknown)}.")
        resolved = self.resolve(values)
        check(parse_overrides(resolved.inputs))
        return resolved.values


def _setting_values(values: Mapping[str, object]) -> dict[str, SettingValue]:
    """Nur Werte, die in JSON und Presets passen (Text, Zahl, Wahrheitswert)."""
    result: dict[str, SettingValue] = {}
    for name, value in values.items():
        if not isinstance(value, str | int | float | bool):
            raise ConfigError(f"Ungültiger Wert für „{name}“: {value!r}.")
        result[name] = value
    return result
