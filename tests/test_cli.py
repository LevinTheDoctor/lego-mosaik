"""Tests für die deutsche CLI mit ``typer.testing.CliRunner``."""

import json
from pathlib import Path

import pytest
import typer
from conftest import FakePriceSource, add_bricklink_colors, make_pipeline, make_price_service
from typer.testing import CliRunner

from lego_mosaik.config import AppConfig
from lego_mosaik.interfaces.cli import create_cli

runner = CliRunner()


@pytest.fixture
def cli(pipeline_config: AppConfig) -> typer.Typer:
    return create_cli(pipeline_config, make_pipeline, price_factory=make_price_service)


@pytest.mark.parametrize("flag", ["-h", "--hilfe", "--help"])
def test_help_flags(cli: typer.Typer, flag: str) -> None:
    result = runner.invoke(cli, [flag])
    assert result.exit_code == 0
    assert "erstellen" in result.output


def test_create(cli: typer.Typer, image_file: Path) -> None:
    result = runner.invoke(cli, ["erstellen", str(image_file), "--rahmen", "2"])
    assert result.exit_code == 0, result.output
    assert "fertig" in result.output
    assert "mosaik.ldr" in result.output


def test_create_with_art_panels_and_custom_size(cli: typer.Typer, image_file: Path) -> None:
    result = runner.invoke(
        cli,
        [
            "erstellen",
            str(image_file),
            "--unterbau",
            "art",
            "--unterbaufarbe",
            "0",
            "--breite",
            "40",
            "--rahmen-teile",
            "tile",
            "--rahmen-erhoehung",
            "1",
            "--eckenfarbe",
            "15",
        ],
    )
    assert result.exit_code == 0, result.output
    # 40 rastet auf ganze 16er-Paneele ein (48), die Höhe kommt aus dem quadratischen Bild
    assert "48 × 48 Noppen" in result.output
    assert "Technic-Pins" in result.output  # 2 × 2 Paneele sind verbunden


def test_help_groups_options(cli: typer.Typer) -> None:
    result = runner.invoke(cli, ["erstellen", "--hilfe"])
    assert "Größe und Unterbau" in result.output and "Rahmen" in result.output


def test_invalid_option_exit_code_2(cli: typer.Typer, image_file: Path) -> None:
    result = runner.invoke(cli, ["erstellen", str(image_file), "--platten", "zwei"])
    assert result.exit_code == 2
    assert "BxH" in result.output


def test_missing_image_exit_code_2(cli: typer.Typer, tmp_path: Path) -> None:
    result = runner.invoke(cli, ["erstellen", str(tmp_path / "fehlt.png")])
    assert result.exit_code == 2
    assert "nicht gefunden" in result.output


def test_unknown_method_lists_valid_names(cli: typer.Typer, image_file: Path) -> None:
    result = runner.invoke(cli, ["erstellen", str(image_file), "--dithering", "zauber"])
    assert result.exit_code == 2
    assert "floyd_steinberg" in result.output


def test_debug_shows_traceback(cli: typer.Typer, tmp_path: Path) -> None:
    result = runner.invoke(cli, ["--debug", "erstellen", str(tmp_path / "fehlt.png")])
    assert result.exit_code == 1
    assert result.exception is not None  # Exception wird weitergeworfen


def test_colors_table(cli: typer.Typer) -> None:
    result = runner.invoke(cli, ["farben", "--teile", "plate"])
    assert result.exit_code == 0
    assert "Black" in result.output and "Trans-Red" not in result.output
    exotic = runner.invoke(cli, ["farben", "--teile", "plate", "--exotische-farben"])
    assert "Trans-Red" in exotic.output


def test_methods(cli: typer.Typer) -> None:
    result = runner.invoke(cli, ["verfahren"])
    assert "floyd_steinberg" in result.output


def test_update_offline_keeps_existing_files(cli: typer.Typer) -> None:
    result = runner.invoke(cli, ["teile-aktualisieren", "--erzwingen"])
    assert result.exit_code == 0  # alte Dateien bleiben nutzbar
    assert "fehlgeschlagen" in result.output


def test_formats_command(cli: typer.Typer) -> None:
    result = runner.invoke(cli, ["formate"])
    assert result.exit_code == 0, result.output
    assert "weltkarte" in result.output and "128 × 80" in result.output


def test_create_with_format_style_and_relief(cli: typer.Typer, image_file: Path) -> None:
    args = ["erstellen", str(image_file), "--format", "klein", "--stil", "formen"]
    result = runner.invoke(cli, [*args, "--relief", "motiv", "--ausgaben", "preview_png"])
    assert result.exit_code == 0, result.output
    assert "32 × 32 Noppen" in result.output


def test_unknown_style_exit_code_2(cli: typer.Typer, image_file: Path) -> None:
    result = runner.invoke(cli, ["erstellen", str(image_file), "--stil", "wellen"])
    assert result.exit_code == 2
    assert "punkte" in result.output  # Meldung nennt die gültigen Stile


def _create_mosaic(cli: typer.Typer, image_file: Path, pipeline_config: AppConfig) -> Path:
    result = runner.invoke(
        cli, ["erstellen", str(image_file), "--ausgaben", "parts_list_csv,result_json"]
    )
    assert result.exit_code == 0, result.output
    (directory,) = pipeline_config.output_dir.iterdir()
    add_bricklink_colors(directory)
    return directory


def test_prices_for_newest_mosaic(
    cli: typer.Typer, image_file: Path, pipeline_config: AppConfig
) -> None:
    directory = _create_mosaic(cli, image_file, pipeline_config)
    result = runner.invoke(cli, ["preise"])
    assert result.exit_code == 0, result.output
    assert "Geschätzter Preis" in result.output and "wanted/upload.page" in result.output
    assert (directory / "preise_bricklink.csv").is_file()


def test_prices_offline_without_cache(
    cli: typer.Typer, image_file: Path, pipeline_config: AppConfig
) -> None:
    directory = _create_mosaic(cli, image_file, pipeline_config)
    result = runner.invoke(cli, ["preise", directory.name, "--offline"])
    assert result.exit_code == 0, result.output
    assert "Unvollständig" in result.output
    prices = json.loads((directory / "preise.json").read_text(encoding="utf-8"))
    assert {line["hinweis"] for line in prices["positionen"]} == {"nicht im Cache (offline)"}


def test_create_with_prices(pipeline_config: AppConfig, image_file: Path) -> None:
    source = FakePriceSource()
    cli = create_cli(
        pipeline_config,
        make_pipeline,
        price_factory=lambda config: make_price_service(config, source),
    )
    result = runner.invoke(cli, ["erstellen", str(image_file), "--preise"])
    assert result.exit_code == 0, result.output
    assert "Geschätzter Preis" in result.output
    assert not source.calls  # Testfarben haben keinen BrickLink-Code: nichts abzufragen


def test_prices_unknown_mosaic_exit_code_2(cli: typer.Typer) -> None:
    result = runner.invoke(cli, ["preise", "gibt_es_nicht"])
    assert result.exit_code == 2
    assert "gibt es nicht" in result.output


# --- Bild bearbeiten, Hintergrund, Paletten, Presets ------------------------------------


def test_create_with_filter_background_and_palette(cli: typer.Typer, image_file: Path) -> None:
    created = runner.invoke(cli, ["paletten", "anlegen", "Kontrast", "--farben", "0,15"])
    assert created.exit_code == 0, created.output
    assert "kontrast" in created.output
    listed = runner.invoke(cli, ["paletten"])
    assert "Kontrast" in listed.output
    result = runner.invoke(
        cli,
        [
            "erstellen",
            str(image_file),
            "--farbfilter",
            "graustufen",
            "--drehen",
            "180",
            "--hintergrund",
            "aufhellen",
            "--palette",
            "kontrast",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "original.png" in result.output and "eingaben.json" in result.output
    unknown = runner.invoke(cli, ["erstellen", str(image_file), "--palette", "fehlt"])
    assert unknown.exit_code == 2 and "gibt es nicht" in unknown.output
    assert runner.invoke(cli, ["paletten", "loeschen", "kontrast"]).exit_code == 0


def test_background_command_saves_mask(cli: typer.Typer, image_file: Path, tmp_path: Path) -> None:
    mask = tmp_path / "maske.png"
    result = runner.invoke(cli, ["hintergrund", str(image_file), "--maske", str(mask)])
    assert result.exit_code == 0, result.output
    assert "LEGO-Farbe" in result.output and "--hintergrund entfernen" in result.output
    assert mask.read_bytes().startswith(b"\x89PNG")


def test_edit_command(cli: typer.Typer, image_file: Path, pipeline_config: AppConfig) -> None:
    assert runner.invoke(cli, ["erstellen", str(image_file)]).exit_code == 0
    result = runner.invoke(
        cli, ["bearbeiten", "--noppe", "2,2=15", "--flaeche", "3,3,2,2=4", "--fuellen", "14,14=0"]
    )
    assert result.exit_code == 0, result.output
    folders = sorted(path.name for path in pipeline_config.output_dir.iterdir())
    assert len(folders) == 2
    raster = json.loads((pipeline_config.output_dir / folders[1] / "eingaben.json").read_text())
    assert [edit["art"] for edit in raster["aenderungen"]] == ["noppe", "flaeche", "fuellen"]
    bad = runner.invoke(cli, ["bearbeiten", "--noppe", "2-2=15"])
    assert bad.exit_code == 2 and "Format" in bad.output
    none = runner.invoke(cli, ["bearbeiten"])
    assert none.exit_code == 2


def test_presets_commands(cli: typer.Typer, image_file: Path) -> None:
    assert "Noch keine Presets" in runner.invoke(cli, ["presets"]).output
    assert runner.invoke(cli, ["erstellen", str(image_file), "--rahmen", "2"]).exit_code == 0
    saved = runner.invoke(cli, ["presets", "speichern", "Breit", "--bewertung", "3"])
    assert saved.exit_code == 0, saved.output
    assert runner.invoke(cli, ["presets", "favorit", "breit"]).exit_code == 0
    assert runner.invoke(cli, ["presets", "bewerten", "breit", "5"]).exit_code == 0
    listed = runner.invoke(cli, ["presets", "--sortierung", "name"])
    assert "Breit" in listed.output and "★★★★★" in listed.output and "rahmen=2" in listed.output
    used = runner.invoke(cli, ["erstellen", str(image_file), "--preset", "breit"])
    assert used.exit_code == 0, used.output
    assert runner.invoke(cli, ["presets", "bewerten", "breit", "7"]).exit_code == 2
    assert runner.invoke(cli, ["presets", "loeschen", "breit"]).exit_code == 0
