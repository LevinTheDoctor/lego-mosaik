"""Tests für die deutsche CLI mit ``typer.testing.CliRunner``."""

from pathlib import Path

import pytest
import typer
from conftest import make_pipeline
from typer.testing import CliRunner

from lego_mosaik.config import AppConfig
from lego_mosaik.interfaces.cli import create_cli

runner = CliRunner()


@pytest.fixture
def cli(pipeline_config: AppConfig) -> typer.Typer:
    return create_cli(pipeline_config, make_pipeline)


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
            "24",
            "--rahmen-teile",
            "tile",
            "--rahmen-erhoehung",
            "1",
            "--eckenfarbe",
            "15",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "24 × 24 Noppen" in result.output  # Höhe aus dem quadratischen Bild
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
