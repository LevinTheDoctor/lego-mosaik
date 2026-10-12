"""Tests für Overrides, Validierung und das Parsen von Eingaben."""

import dataclasses

import pytest

from lego_mosaik.config import (
    AppConfig,
    FitMode,
    MosaicOverrides,
    apply_overrides,
    parse_fit_mode,
    parse_part_kinds,
    validate_config,
)
from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.models import PartKind


def test_overrides_change_only_given_values(app_config: AppConfig) -> None:
    overrides = MosaicOverrides(width_studs=64, fit_mode=FitMode.PAD, strength=0.0)
    run_config = apply_overrides(app_config, overrides)
    assert run_config.mosaic.width_studs == 64
    assert run_config.mosaic.height_studs == app_config.mosaic.height_studs
    assert run_config.mosaic.fit_mode is FitMode.PAD
    # 0.0 ist ein gültiger Wert und darf nicht durch den Standard ersetzt werden
    assert run_config.dithering.strength == 0.0
    # Das Original bleibt unverändert (frozen + replace)
    assert app_config.mosaic.width_studs == 8


def test_empty_overrides_keep_config(app_config: AppConfig) -> None:
    assert apply_overrides(app_config, MosaicOverrides()) == app_config


def test_width_out_of_range_has_german_message(app_config: AppConfig) -> None:
    with pytest.raises(ConfigError, match="Breite muss zwischen 8 und 256 Noppen liegen"):
        apply_overrides(app_config, MosaicOverrides(width_studs=7))


def test_strength_out_of_range(app_config: AppConfig) -> None:
    with pytest.raises(ConfigError, match="Dithering-Stärke"):
        apply_overrides(app_config, MosaicOverrides(strength=1.5))


def test_duplicate_outputs_rejected(app_config: AppConfig) -> None:
    with pytest.raises(ConfigError, match="nur einmal"):
        validate_config(dataclasses.replace(app_config, outputs=("ldraw", "ldraw")))


def test_invalid_bayer_size_rejected(app_config: AppConfig) -> None:
    dithering = dataclasses.replace(app_config.dithering, bayer_size=3)
    with pytest.raises(ConfigError, match="Bayer"):
        validate_config(dataclasses.replace(app_config, dithering=dithering))


def test_parse_part_kinds() -> None:
    assert parse_part_kinds(" Plate, tile ") == (PartKind.PLATE, PartKind.TILE)


def test_parse_part_kinds_lists_valid_names() -> None:
    with pytest.raises(ConfigError, match="Gültig sind: brick, plate, tile"):
        parse_part_kinds("plate,stein")


def test_parse_fit_mode_uses_german_labels() -> None:
    assert parse_fit_mode("zuschneiden") is FitMode.CROP
    assert parse_fit_mode("Einpassen") is FitMode.PAD
    with pytest.raises(ConfigError, match="einpassen, zuschneiden"):
        parse_fit_mode("crop")
