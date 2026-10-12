"""Deutsche Kommandozeile (Typer + Rich).

Die CLI enthält keine Fachlogik: Sie liest Optionen, übersetzt sie in
``MosaicOverrides``, ruft die Pipeline auf und gibt das Ergebnis aus.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

lazy import typer
lazy from rich.console import Console
lazy from rich.logging import RichHandler
lazy from rich.table import Table

from lego_mosaik.config import (
    EXOTIC_CATEGORIES,
    AppConfig,
    MosaicOverrides,
    parse_color_categories,
    parse_fit_mode,
    parse_names,
    parse_part_kinds,
    parse_plates,
)
from lego_mosaik.domain.errors import ConfigError, InvalidImageError, LegoMosaicError
from lego_mosaik.domain.models import ColorCategory, LegoColor
lazy from lego_mosaik.pipeline import MosaicPipeline, MosaicResult
lazy from lego_mosaik.sources.protocol import FileStatus, UpdateReport

# Exit-Codes laut Anforderung
EXIT_PROCESSING_ERROR = 1
EXIT_INVALID_INPUT = 2
SWATCH = "      "  # Leerzeichen, die mit der Farbe als Hintergrund ein Farbfeld ergeben

type PipelineFactory = Callable[[AppConfig], MosaicPipeline]
type ServeFunction = Callable[[AppConfig, str, int], None]


@dataclass(slots=True)
class _State:
    """Globale Optionen (z. B. ``--debug``), die der Callback für alle Befehle setzt."""

    debug: bool = False


def create_cli(
    config: AppConfig, pipeline_factory: PipelineFactory, serve: ServeFunction | None = None
) -> typer.Typer:
    """Baut die Typer-App (Factory). Konfiguration und Pipeline kommen von außen (``main.py``).

    Args:
        config: Standardkonfiguration.
        pipeline_factory: Erzeugt die Pipeline (erst bei Bedarf, damit ``--hilfe`` schnell bleibt).
        serve: Startet den HTTP-Server; ``None`` blendet den Befehl ``server`` aus.
    """
    app = typer.Typer(
        context_settings={"help_option_names": ["-h", "--hilfe", "--help"]},
        no_args_is_help=True,
        add_completion=False,
        help="Erzeugt aus einem Bild ein baubares LEGO-Mosaik aus echten Teilen.",
    )
    state = _State()
    console = Console()

    @app.callback()
    def main(
        debug: Annotated[
            bool, typer.Option("--debug", help="Ausführliche Logs und Tracebacks anzeigen.")
        ] = False,
    ) -> None:
        """LEGO-Mosaik-Service: Bild → Mosaik → LDraw, Teilelisten, Vorschau."""
        state.debug = debug
        _configure_logging(debug)

    _register_create(app, config, pipeline_factory, state, console)
    _register_catalog_commands(app, config, pipeline_factory, state, console)
    if serve is not None:
        _register_server(app, config, serve, state)
    return app


def _configure_logging(debug: bool) -> None:
    """Logs über Rich; ohne ``--debug`` nur Warnungen, damit die Ausgabe ruhig bleibt."""
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(show_path=False, show_time=False)],
        force=True,  # ersetzt Handler aus früheren Aufrufen (z. B. in Tests)
    )


def _run_safely[T](state: _State, console: Console, action: Callable[[], T]) -> T:
    """Führt ``action`` aus und übersetzt fachliche Fehler in Meldung + Exit-Code.

    Ungültige Eingaben (Konfiguration, Bild) ergeben Exit-Code 2, alle anderen
    fachlichen Fehler Exit-Code 1. Mit ``--debug`` wird der Fehler weitergeworfen,
    damit der Traceback sichtbar ist.
    """
    try:
        return action()
    except LegoMosaicError as error:
        if state.debug:
            raise
        console.print(f"[bold red]Fehler:[/bold red] {error}")
        invalid_input = isinstance(error, (ConfigError, InvalidImageError))
        raise typer.Exit(EXIT_INVALID_INPUT if invalid_input else EXIT_PROCESSING_ERROR) from None


# --- erstellen ---------------------------------------------------------------------


def _register_create(
    app: typer.Typer,
    config: AppConfig,
    pipeline_factory: PipelineFactory,
    state: _State,
    console: Console,
) -> None:
    """Registriert den Befehl ``erstellen``."""

    @app.command("erstellen")
    def create(
        bild: Annotated[Path, typer.Argument(help="Bilddatei (JPG, PNG, …).")],
        grundplatte: Annotated[
            int | None, typer.Option(help="Grundplattengröße in Noppen: 16, 32 oder 48.")
        ] = None,
        platten: Annotated[
            str | None, typer.Option(help="Anzahl Grundplatten als BxH, z. B. 2x2.")
        ] = None,
        rahmen: Annotated[
            int | None, typer.Option(help="Rahmenbreite in Noppen (0 = ohne).")
        ] = None,
        rahmenfarbe: Annotated[
            int | None, typer.Option(help="Rahmenfarbe (Rebrickable-ID).")
        ] = None,
        mit_grundplatte: Annotated[
            bool | None,
            typer.Option(
                "--mit-grundplatte/--ohne-grundplatte",
                help="Grundplatten in LDraw-Datei und Teilelisten aufnehmen.",
            ),
        ] = None,
        modus: Annotated[str | None, typer.Option(help="zuschneiden oder einpassen.")] = None,
        teile: Annotated[str | None, typer.Option(help="Teilarten, z. B. plate,tile.")] = None,
        farbkategorien: Annotated[
            str | None, typer.Option(help="Farbkategorien, z. B. solid,metallic,pearl.")
        ] = None,
        exotische_farben: Annotated[
            bool, typer.Option("--exotische-farben", help="Alle Kategorien außer Gummi erlauben.")
        ] = False,
        dithering: Annotated[str | None, typer.Option(help="Dithering-Verfahren.")] = None,
        staerke: Annotated[
            float | None, typer.Option(help="Dithering-Stärke von 0.0 bis 1.0.")
        ] = None,
        zerlegung: Annotated[str | None, typer.Option(help="Zerlegungsverfahren.")] = None,
        ausgaben: Annotated[
            str | None, typer.Option(help="Ausgaben, z. B. preview_png,ldraw.")
        ] = None,
        ziel: Annotated[Path | None, typer.Option(help="Ausgabeordner.")] = None,
    ) -> None:
        """Erzeugt ein Mosaik aus BILD (Optionen ohne Angabe kommen aus der Konfiguration)."""

        def action() -> MosaicResult:
            plates = parse_plates(platten) if platten else (None, None)
            overrides = MosaicOverrides(
                baseplate_studs=grundplatte,
                plates_wide=plates[0],
                plates_high=plates[1],
                include_baseplate=mit_grundplatte,
                frame_width=rahmen,
                frame_color_id=rahmenfarbe,
                fit_mode=parse_fit_mode(modus) if modus else None,
                part_kinds=parse_part_kinds(teile) if teile else None,
                color_categories=_categories(farbkategorien, exotic=exotische_farben),
                dithering_method=dithering,
                strength=staerke,
                tiling_strategy=zerlegung,
                outputs=parse_names(ausgaben) if ausgaben else None,
                output_dir=ziel,
            )
            pipeline = pipeline_factory(config)
            with console.status("Starte …") as status:
                return pipeline.run(
                    bild, overrides, title=bild.stem, on_step=lambda label: status.update(label)
                )

        result = _run_safely(state, console, action)
        _print_summary(console, result)


def _categories(text: str | None, *, exotic: bool) -> tuple[ColorCategory, ...] | None:
    """Farbkategorien aus ``--farbkategorien`` und ``--exotische-farben`` (vereinigt)."""
    if text is None and not exotic:
        return None
    chosen = list(parse_color_categories(text)) if text else [ColorCategory.SOLID]
    if exotic:
        chosen += [category for category in EXOTIC_CATEGORIES if category not in chosen]
    return tuple(chosen)


def _print_summary(console: Console, result: MosaicResult) -> None:
    """Zusammenfassung: Größe, Farben, Teile, Einsparung, erzeugte Dateien."""
    stats, mosaic = result.stats, result.mosaic
    table = Table(title=f"Mosaik „{mosaic.title}“ fertig", show_header=False)
    table.add_row("Größe", f"{mosaic.width} × {mosaic.height} Noppen")
    table.add_row("Bildfläche", f"{stats['bild_breite']} × {stats['bild_hoehe']} Noppen")
    table.add_row("Grundplatten", str(len(mosaic.baseplates)))
    table.add_row("Farben", str(stats["farben"]))
    table.add_row("Teile", f"{stats['parts']} (ohne Grundplatten)")
    table.add_row("Einsparung", f"{stats['savings_percent']} % gegenüber nur 1×1")
    durations = stats["dauer_sekunden"]
    assert isinstance(durations, dict)
    table.add_row("Dauer", f"{sum(durations.values()):.1f} s")
    table.add_row("Ordner", str(result.target_dir))
    console.print(table)
    for path in sorted(result.target_dir.iterdir()):
        console.print(f"  • {path.name}")


# --- Katalogbefehle ------------------------------------------------------------------


def _register_catalog_commands(
    app: typer.Typer,
    config: AppConfig,
    pipeline_factory: PipelineFactory,
    state: _State,
    console: Console,
) -> None:
    """Registriert ``teile-aktualisieren``, ``farben`` und ``verfahren``."""

    @app.command("teile-aktualisieren")
    def update(
        erzwingen: Annotated[
            bool, typer.Option("--erzwingen", help="Alle Dateien neu laden, auch wenn aktuell.")
        ] = False,
    ) -> None:
        """Lädt die Rebrickable-CSV-Dateien herunter, wenn sie fehlen oder veraltet sind."""
        report = _run_safely(
            state, console, lambda: pipeline_factory(config).update_catalog(force=erzwingen)
        )
        _print_update_report(console, report)
        _run_safely(state, console, report.raise_if_unusable)

    @app.command("farben")
    def colors(
        teile: Annotated[str | None, typer.Option(help="Teilarten, z. B. plate,tile.")] = None,
        farbkategorien: Annotated[
            str | None, typer.Option(help="Farbkategorien, z. B. solid,pearl.")
        ] = None,
        exotische_farben: Annotated[
            bool, typer.Option("--exotische-farben", help="Alle Kategorien außer Gummi.")
        ] = False,
    ) -> None:
        """Zeigt die verfügbare Palette als Tabelle mit Farbfeld."""

        def action() -> tuple[LegoColor, ...]:
            overrides = MosaicOverrides(
                part_kinds=parse_part_kinds(teile) if teile else None,
                color_categories=_categories(farbkategorien, exotic=exotische_farben),
            )
            return pipeline_factory(config).palette(overrides)

        _print_palette(console, _run_safely(state, console, action))

    @app.command("verfahren")
    def methods() -> None:
        """Listet die registrierten Dithering-, Zerlegungs- und Ausgabe-Namen auf."""
        pipeline = _run_safely(state, console, lambda: pipeline_factory(config))
        for kind, names in pipeline.method_names.items():
            console.print(f"[bold]{kind}:[/bold] {', '.join(names)}")


def _print_update_report(console: Console, report: UpdateReport) -> None:
    """Tabelle mit dem Status jeder Datei."""
    styles = {FileStatus.DOWNLOADED: "green", FileStatus.UP_TO_DATE: "cyan"}
    table = Table("Datei", "Status", "Hinweis", title="Teilekatalog")
    for update in report.files:
        style = styles.get(update.status, "red")
        table.add_row(update.file_name, f"[{style}]{update.status.value}[/{style}]", update.message)
    console.print(table)


def _print_palette(console: Console, palette: tuple[LegoColor, ...]) -> None:
    """Tabelle der Palette mit Farbfeld, IDs und Kategorie."""
    table = Table(
        "",
        "ID",
        "Name",
        "Kategorie",
        "LDraw",
        "BrickLink",
        "LEGO-Name",
        title=f"Palette ({len(palette)} Farben)",
    )
    for color in palette:
        table.add_row(
            f"[on {color.hex}]{SWATCH}[/]",
            str(color.id),
            color.name,
            color.category.value,
            str(color.ldraw_code),
            "" if color.bricklink_code is None else str(color.bricklink_code),
            color.lego_name or "",
        )
    console.print(table)


# --- server ----------------------------------------------------------------------------


def _register_server(
    app: typer.Typer, config: AppConfig, serve: ServeFunction, state: _State
) -> None:
    """Registriert ``server``."""

    @app.command("server")
    def server(
        host: Annotated[str | None, typer.Option(help="Adresse (Standard: 127.0.0.1).")] = None,
        port: Annotated[int | None, typer.Option(help="Port (Standard: 8000).")] = None,
    ) -> None:
        """Startet die lokale HTTP-API (FastAPI)."""
        serve(config, host or config.server.host, port or config.server.port)
