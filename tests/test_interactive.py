"""Tests für den interaktiven Modus (Eingaben werden über ``input=`` simuliert)."""

from pathlib import Path

import pytest
import typer
from conftest import make_pipeline
from typer.testing import CliRunner

from lego_mosaik.config import AppConfig
from lego_mosaik.interfaces.cli import create_cli
from lego_mosaik.interfaces.display import UPPER_HALF_BLOCK, mosaic_preview

runner = CliRunner()

# Menüpunkte (siehe InteractiveSession._actions)
LOAD, SIZE, FRAME, STYLE, SETTINGS, PREVIEW, CREATE, PALETTE, FORMATS, QUIT = (
    "1",
    "2",
    "3",
    "6",
    "9",
    "10",
    "11",
    "12",
    "13",
    "0",
)


@pytest.fixture
def cli(pipeline_config: AppConfig) -> typer.Typer:
    return create_cli(pipeline_config, make_pipeline)


def _run(cli: typer.Typer, *answers: str, args: tuple[str, ...] = ()) -> str:
    """Startet den interaktiven Modus und tippt die Antworten nacheinander ein."""
    result = runner.invoke(cli, ["interaktiv", *args], input="\n".join(answers) + "\n")
    assert result.exit_code == 0, result.output
    return result.output


def test_load_preview_and_create(cli: typer.Typer, image_file: Path) -> None:
    output = _run(
        cli,
        LOAD,
        str(image_file),
        FRAME,
        "2",  # Rahmenbreite
        "",  # Rahmenfarbe beibehalten (Black)
        "15",  # Ecken in White
        "tile",  # glatter Rahmen
        "1",  # eine Plate erhöht
        PREVIEW,
        CREATE,
        QUIT,
    )
    assert "Bild geladen" in output and "32 × 32 Pixel" in output
    assert "Übernommen" in output
    assert UPPER_HALF_BLOCK in output  # Vorschau im Terminal
    assert "fertig" in output and "mosaik.ldr" in output
    assert "Tschüss" in output


def test_image_can_be_passed_as_argument(cli: typer.Typer, image_file: Path) -> None:
    output = _run(cli, SETTINGS, QUIT, args=(str(image_file),))
    assert "Bild geladen" in output
    assert "Aktuelle Einstellungen" in output


def test_invalid_values_are_rejected(cli: typer.Typer) -> None:
    output = _run(
        cli,
        FRAME,
        "abc",  # keine Zahl → nochmal fragen
        "20",  # zu breit für 16 × 16 → ganze Gruppe wird verworfen
        "",
        "",
        "",
        "",
        QUIT,
    )
    assert "Ungültige Eingabe „abc“" in output
    assert "Rahmen ist zu breit" in output


def test_custom_size_and_reset(cli: typer.Typer, image_file: Path) -> None:
    output = _run(
        cli,
        LOAD,
        str(image_file),
        SIZE,
        "",  # Unterbau
        "",  # Plattengröße
        "",  # Format
        "",  # Platten
        "30",  # eigene Breite, rastet auf zwei 16er-Platten ein (32)
        "",  # Höhe aus dem Seitenverhältnis (quadratisch → 32)
        "",  # Farbe
        PREVIEW,
        QUIT,
    )
    assert "32 × 32 Noppen" in output


def test_preview_and_create_need_an_image(cli: typer.Typer) -> None:
    output = _run(cli, PREVIEW, CREATE, QUIT)
    assert output.count("Bitte zuerst ein Bild laden") == 2


def test_missing_image_is_reported(cli: typer.Typer, tmp_path: Path) -> None:
    output = _run(cli, LOAD, str(tmp_path / "fehlt.png"), PALETTE, QUIT)
    assert "nicht gefunden" in output
    assert "Palette" in output  # das Menü läuft nach dem Fehler weiter


def test_end_of_input_quits(cli: typer.Typer) -> None:
    result = runner.invoke(cli, ["interaktiv"], input="")
    assert result.exit_code == 0
    assert "Tschüss" in result.output


def test_mosaic_preview_shrinks_wide_mosaics(pipeline_config: AppConfig, image_file: Path) -> None:
    mosaic = make_pipeline(pipeline_config).preview(image_file)  # 16 × 16
    full = mosaic_preview(mosaic, max_columns=80).plain.splitlines()
    assert len(full) == 8 and all(len(line) == 16 for line in full)  # 2 Zeilen pro Zeichen
    half = mosaic_preview(mosaic, max_columns=8).plain.splitlines()
    assert all(len(line) == 8 for line in half)  # nur jede 2. Noppe


def test_end_of_input_inside_a_group_quits(cli: typer.Typer) -> None:
    result = runner.invoke(cli, ["interaktiv"], input=f"{FRAME}\n2\n")  # Eingabe endet mitten drin
    assert result.exit_code == 0
    assert "Tschüss" in result.output


def test_formats_are_suggested_and_listed(cli: typer.Typer, image_file: Path) -> None:
    output = _run(cli, LOAD, str(image_file), FORMATS, QUIT)
    assert "Passende Formate: mini (16 × 16)" in output  # quadratisches Bild
    assert "weltkarte" in output


def test_format_replaces_custom_size(cli: typer.Typer, image_file: Path) -> None:
    size_answers = ("", "", "", "", "50", "", "")  # eigene Breite 50 (rastet auf 48 ein)
    format_answers = ("", "", "klein", "", "", "", "")  # danach Format „klein“
    output = _run(
        cli, LOAD, str(image_file), SIZE, *size_answers, SIZE, *format_answers, PREVIEW, QUIT
    )
    assert output.count("Übernommen") == 2
    assert "32 × 32 Noppen" in output  # Format 2 × 2 Platten à 16 Noppen


def test_style_and_relief_group(cli: typer.Typer, image_file: Path) -> None:
    output = _run(
        cli, LOAD, str(image_file), STYLE, "punkte", "motiv", "2", SETTINGS, PREVIEW, QUIT
    )
    assert "Übernommen" in output
    assert "punkte" in output and "motiv" in output
    assert UPPER_HALF_BLOCK in output
