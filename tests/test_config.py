"""Tests für Overrides, Validierung und das Parsen von Eingaben."""

import dataclasses

import pytest

from lego_mosaik.config import (
    AppConfig,
    MosaicOverrides,
    OverrideInput,
    ServerConfig,
    apply_overrides,
    form_values,
    override_input,
    parse_fit_mode,
    parse_overrides,
    parse_part_kinds,
    parse_plates,
    substrate_color_id,
    validate_config,
)
from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.models import (
    BackgroundMode,
    ColorDistance,
    ColorFilter,
    FitMode,
    PartKind,
    Smoothing,
    Substrate,
)


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
    overrides = MosaicOverrides(plate_studs=48, plates_wide=9)  # 432 Noppen
    with pytest.raises(ConfigError, match="höchstens 384 Noppen pro Seite"):
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


def test_parse_smoothing_labels(app_config: AppConfig) -> None:
    overrides = parse_overrides(OverrideInput(smoothing="Stark"))
    assert overrides.smoothing is Smoothing.STRONG
    assert apply_overrides(app_config, overrides).dithering.smoothing is Smoothing.STRONG
    assert app_config.dithering.smoothing is Smoothing.LIGHT  # Standard
    with pytest.raises(ConfigError, match="aus, leicht, mittel, stark"):
        parse_overrides(OverrideInput(smoothing="maximal"))


def test_max_colors_zero_means_unlimited(app_config: AppConfig) -> None:
    limited = dataclasses.replace(
        app_config, palette=dataclasses.replace(app_config.palette, max_colors=5)
    )
    assert apply_overrides(limited, MosaicOverrides(max_colors=0)).palette.max_colors is None


def test_custom_size_is_checked(app_config: AppConfig) -> None:
    run_config = apply_overrides(app_config, MosaicOverrides(width_studs=50))
    assert run_config.layout.custom_size and run_config.layout.height_studs is None
    with pytest.raises(ConfigError, match="Breite muss zwischen 8 und 384"):
        apply_overrides(app_config, MosaicOverrides(width_studs=400))


def test_frame_raise_is_limited(app_config: AppConfig) -> None:
    with pytest.raises(ConfigError, match="Rahmen-Erhöhung"):
        apply_overrides(app_config, MosaicOverrides(frame_raise_plates=9))


def test_substrate_color_defaults_per_substrate(app_config: AppConfig) -> None:
    layout = app_config.layout
    assert substrate_color_id(layout) == 71  # Grundplatte: Light Bluish Gray
    art = dataclasses.replace(layout, substrate=Substrate.ART_PANEL)
    assert substrate_color_id(art) == 0  # Art-Paneel: Black
    assert substrate_color_id(dataclasses.replace(art, substrate_color_id=15)) == 15


@pytest.mark.parametrize(
    ("server", "message"),
    [
        (ServerConfig(cors_origins=("http://localhost:5173/app",)), "CORS-Origin"),
        (ServerConfig(cors_origins=("localhost:5173",)), "CORS-Origin"),
        (ServerConfig(cors_origin_regex="(localhost"), "CORS-Regex"),
    ],
)
def test_invalid_cors_settings_rejected(
    app_config: AppConfig, server: ServerConfig, message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        validate_config(dataclasses.replace(app_config, server=server))


# --- Bildbearbeitung, eigene Palette, Formularfelder ------------------------------------


def test_image_editing_inputs_are_parsed() -> None:
    raw = OverrideInput(
        color_filter="Sepia",
        rotate=90,
        mirror=True,
        crop="10,0,80%,100",
        background="entfernen",
        background_color_id=1,
        color_ids="0, 15,71",
    )
    overrides = parse_overrides(raw)
    assert overrides.color_filter is ColorFilter.SEPIA
    assert overrides.crop == (10.0, 0.0, 80.0, 100.0)
    assert overrides.background is BackgroundMode.REMOVE
    assert overrides.only_color_ids == (0, 15, 71)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (OverrideInput(color_filter="bunt"), "Unbekannte Farbfilter"),
        (OverrideInput(background="weg"), "Unbekannte Hintergrund-Art"),
        (OverrideInput(crop="10,10"), "Ausschnitt bitte"),
        (OverrideInput(color_ids="rot,blau"), "Farb-IDs"),
    ],
)
def test_invalid_image_inputs_are_rejected(raw: OverrideInput, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_overrides(raw)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (MosaicOverrides(rotate=45), "Drehen geht nur"),
        (MosaicOverrides(crop=(50.0, 0.0, 60.0, 100.0)), "Ausschnitt muss im Bild"),
        (MosaicOverrides(filter_strength=1.5), "Filterstärke"),
        (MosaicOverrides(brightness=0.0), "Helligkeit"),
        (MosaicOverrides(background_tolerance=150.0), "Hintergrund-Toleranz"),
        (MosaicOverrides(only_color_ids=(0, 0)), "Eigene Palette"),
    ],
)
def test_invalid_image_values_are_rejected(
    app_config: AppConfig, overrides: MosaicOverrides, message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        apply_overrides(app_config, overrides)


def test_form_fields_round_trip() -> None:
    values = {"stil": "punkte", "kontrast": 1, "spiegeln": True, "exotische_farben": True}
    raw = override_input(values)
    assert raw.contrast == 1.0 and isinstance(raw.contrast, float)  # 1 → 1.0
    assert form_values(raw) == {**values, "kontrast": 1.0}
    with pytest.raises(ConfigError, match="Unbekannte Felder: farbe"):
        override_input({"farbe": "rot"})
    with pytest.raises(ConfigError, match="Ungültiger Wert für „breite“"):
        override_input({"breite": True})
