"""Tests für Overrides, Validierung und das Parsen von Eingaben."""

import dataclasses

import pytest

from lego_mosaik.config import (
    AppConfig,
    MosaicOverrides,
    OverrideInput,
    apply_overrides,
    parse_fit_mode,
    parse_overrides,
    parse_part_kinds,
    parse_plates,
    substrate_color_id,
    validate_config,
)
from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.models import ColorDistance, FitMode, PartKind, Substrate


def test_overrides_change_only_given_values(app_config: AppConfig) -> None:
    overrides = MosaicOverrides(plates_wide=2, fit_mode=FitMode.PAD, strength=0.0, frame_width=0)
    run_config = apply_overrides(app_config, overrides)
    assert run_config.layout.plates_wide == 2
    assert run_config.layout.plates_high == app_config.layout.plates_high
    assert run_config.mosaic.fit_mode is FitMode.PAD
    # 0 bzw. 0.0 sind gültige Werte und dürfen nicht durch den Standard ersetzt werden
    assert run_config.dithering.strength == 0.0
    assert run_config.frame.width == 0
    # Das Original bleibt unverändert (frozen + replace)
    assert app_config.layout.plates_wide == 1


def test_empty_overrides_keep_config(app_config: AppConfig) -> None:
    assert apply_overrides(app_config, MosaicOverrides()) == app_config


def test_too_many_studs_rejected(app_config: AppConfig) -> None:
    overrides = MosaicOverrides(plate_studs=48, plates_wide=6)
    with pytest.raises(ConfigError, match="höchstens 256 Noppen pro Seite"):
        apply_overrides(app_config, overrides)


def test_frame_too_wide_rejected(app_config: AppConfig) -> None:
    with pytest.raises(ConfigError, match="Rahmen ist zu breit"):
        apply_overrides(app_config, MosaicOverrides(frame_width=7))  # 16 − 14 = 2 < 4


def test_baseplate_is_no_mosaic_part_kind(app_config: AppConfig) -> None:
    with pytest.raises(ConfigError, match="Ungültige Teilart baseplate"):
        apply_overrides(app_config, MosaicOverrides(part_kinds=(PartKind.BASEPLATE,)))


def test_parse_plates() -> None:
    assert parse_plates("2x3") == (2, 3)
    assert parse_plates(" 1 × 1 ") == (1, 1)
    with pytest.raises(ConfigError, match="BxH"):
        parse_plates("zwei")


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


def test_parse_overrides_translates_german_labels() -> None:
    raw = OverrideInput(
        substrate="Art",
        color_distance="cie76",
        frame_part_kinds="tile",
        plates="2x3",
        max_colors=0,
    )
    overrides = parse_overrides(raw)
    assert overrides.substrate is Substrate.ART_PANEL
    assert overrides.color_distance is ColorDistance.CIE76
    assert overrides.frame_part_kinds == (PartKind.TILE,)
    assert (overrides.plates_wide, overrides.plates_high) == (2, 3)
    with pytest.raises(ConfigError, match="art, grundplatte, ohne"):
        parse_overrides(OverrideInput(substrate="holz"))


def test_max_colors_zero_means_unlimited(app_config: AppConfig) -> None:
    limited = dataclasses.replace(
        app_config, palette=dataclasses.replace(app_config.palette, max_colors=5)
    )
    assert apply_overrides(limited, MosaicOverrides(max_colors=0)).palette.max_colors is None


def test_custom_size_is_checked(app_config: AppConfig) -> None:
    run_config = apply_overrides(app_config, MosaicOverrides(width_studs=50))
    assert run_config.layout.custom_size and run_config.layout.height_studs is None
    with pytest.raises(ConfigError, match="Breite muss zwischen 8 und 256"):
        apply_overrides(app_config, MosaicOverrides(width_studs=300))


def test_frame_raise_is_limited(app_config: AppConfig) -> None:
    with pytest.raises(ConfigError, match="Rahmen-Erhöhung"):
        apply_overrides(app_config, MosaicOverrides(frame_raise_plates=9))


def test_substrate_color_defaults_per_substrate(app_config: AppConfig) -> None:
    layout = app_config.layout
    assert substrate_color_id(layout) == 71  # Grundplatte: Light Bluish Gray
    art = dataclasses.replace(layout, substrate=Substrate.ART_PANEL)
    assert substrate_color_id(art) == 0  # Art-Paneel: Black
    assert substrate_color_id(dataclasses.replace(art, substrate_color_id=15)) == 15
