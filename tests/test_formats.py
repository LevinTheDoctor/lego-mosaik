"""Tests für Formate und das Einrasten auf ganze Platten."""

import dataclasses

import pytest

from lego_mosaik.config import (
    AppConfig,
    MosaicOverrides,
    OverrideInput,
    apply_overrides,
    parse_overrides,
)
from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.layout import (
    FORMATS,
    complete_size,
    find_format,
    snap_to_plates,
    suggest_formats,
)
from lego_mosaik.domain.models import PartKind, Substrate


@pytest.mark.parametrize(
    ("studs", "expected"), [(40, 48), (48, 48), (23, 16), (24, 32), (8, 16), (1, 16)]
)
def test_snap_to_whole_panels(studs: int, expected: int) -> None:
    assert snap_to_plates(studs, 16) == expected


def test_complete_size_snaps_both_sides() -> None:
    # Breite 40 → 48; Bildfläche 46 breit bei 3:2 → 31 hoch + Rahmen = 33 → 32
    assert complete_size(40, None, 1.5, 1, plate_studs=16) == (48, 32)
    assert complete_size(40, None, 1.5, 1) == (40, 27)  # ohne Unterbau exakt


def test_formats_are_unique_and_known() -> None:
    names = [art_format.name for art_format in FORMATS]
    assert len(names) == len(set(names))
    quadrat = find_format(" Quadrat ")
    assert (quadrat.plates_wide, quadrat.plates_high) == (3, 3)
    weltkarte = find_format("weltkarte")
    assert (weltkarte.plates_wide * 16, weltkarte.plates_high * 16) == (128, 80)


def test_unknown_format_lists_valid_names() -> None:
    with pytest.raises(ConfigError, match="weltkarte"):
        find_format("riesenrad")


def test_suggest_formats_by_aspect_ratio() -> None:
    assert suggest_formats(1.0)[0].name == "mini"  # quadratisch, kleinstes zuerst
    assert suggest_formats(2.0)[0].name == "panorama"
    assert suggest_formats(0.75)[0].name == "hoch"


def test_format_override_sets_plates(app_config: AppConfig) -> None:
    overrides = parse_overrides(OverrideInput(art_format="quer"))
    assert (overrides.plates_wide, overrides.plates_high) == (4, 3)
    config = apply_overrides(app_config, overrides)
    assert (config.layout.plates_wide, config.layout.plates_high) == (4, 3)


def test_format_and_custom_size_conflict() -> None:
    with pytest.raises(ConfigError, match="entweder"):
        parse_overrides(OverrideInput(art_format="quer", plates="2x2"))
    with pytest.raises(ConfigError, match="entweder"):
        parse_overrides(OverrideInput(art_format="quer", width_studs=40))


def test_custom_size_is_validated_after_snapping(app_config: AppConfig) -> None:
    # 8 × 8 mit Rahmen 3 ließe innen nur 2 Noppen; eingerastet sind es 16 × 16, innen 10
    overrides = MosaicOverrides(width_studs=8, height_studs=8, frame_width=3)
    assert apply_overrides(app_config, overrides).layout.width_studs == 8
    without = MosaicOverrides(
        substrate=Substrate.NONE, width_studs=8, height_studs=8, frame_width=3
    )
    with pytest.raises(ConfigError, match="Rahmen"):
        apply_overrides(app_config, without)


def test_dots_need_a_substrate(app_config: AppConfig) -> None:
    overrides = MosaicOverrides(style="punkte", substrate=Substrate.NONE)
    with pytest.raises(ConfigError, match="Unterbau"):
        apply_overrides(app_config, overrides)
    # „formen“ geht auch ohne: Formteile liegen dann immer auf einer Unterlage
    no_substrate = MosaicOverrides(style="formen", substrate=Substrate.NONE)
    assert apply_overrides(app_config, no_substrate).shapes.style == "formen"


def test_shapes_do_not_mix_with_bricks(app_config: AppConfig) -> None:
    overrides = MosaicOverrides(style="punkte", part_kinds=(PartKind.BRICK,))
    with pytest.raises(ConfigError, match="Bricks"):
        apply_overrides(app_config, overrides)


def test_relief_levels_are_limited(app_config: AppConfig) -> None:
    with pytest.raises(ConfigError, match="Relief-Höhe"):
        apply_overrides(app_config, MosaicOverrides(relief_levels=4))


def test_plain_style_works_without_substrate(app_config: AppConfig) -> None:
    config = dataclasses.replace(app_config)
    result = apply_overrides(config, MosaicOverrides(substrate=Substrate.NONE))
    assert result.shapes.style == "quadrat"
