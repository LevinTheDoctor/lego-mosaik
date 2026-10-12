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

from lego_mosaik.config import AppConfig, OverrideInput, parse_overrides
from lego_mosaik.domain.errors import ConfigError, InvalidImageError, LegoMosaicError
from lego_mosaik.domain.models import LegoColor
from lego_mosaik.interfaces.display import print_palette, print_summary
lazy from lego_mosaik.interfaces.interactive import InteractiveSession
lazy from lego_mosaik.pipeline import MosaicPipeline, MosaicResult
lazy from lego_mosaik.sources.protocol import FileStatus, UpdateReport

# Exit-Codes laut Anforderung
EXIT_PROCESSING_ERROR = 1
EXIT_INVALID_INPUT = 2

type PipelineFactory = Callable[[AppConfig], MosaicPipeline]
type ServeFunction = Callable[[AppConfig, str, int], None]

# Gruppen in der Hilfe von „erstellen“ (rich_help_panel), damit die vielen Optionen lesbar bleiben
SIZE_PANEL = "Größe und Unterbau"
FRAME_PANEL = "Rahmen"
IMAGE_PANEL = "Bild und Farben"
METHOD_PANEL = "Verfahren und Ausgaben"


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
    _register_interactive(app, config, pipeline_factory, state, console)
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
        unterbau: Annotated[
            str | None,
            typer.Option(
                help="art (LEGO-Art-Paneele, mit Pins verbunden), grundplatte oder ohne.",
                rich_help_panel=SIZE_PANEL,
            ),
        ] = None,
        plattengroesse: Annotated[
            int | None,
            typer.Option(
                help="Plattengröße in Noppen (Art-Paneel 16; Grundplatte 16, 32, 48).",
                rich_help_panel=SIZE_PANEL,
            ),
        ] = None,
        platten: Annotated[
            str | None,
            typer.Option(help="Anzahl Platten als BxH, z. B. 3x3.", rich_help_panel=SIZE_PANEL),
        ] = None,
        breite: Annotated[
            int | None,
            typer.Option(
                help="Eigene Breite in Noppen (statt Platten).", rich_help_panel=SIZE_PANEL
            ),
        ] = None,
        hoehe: Annotated[
            int | None,
            typer.Option(
                help="Eigene Höhe in Noppen; fehlt sie, kommt sie aus dem Seitenverhältnis.",
                rich_help_panel=SIZE_PANEL,
            ),
        ] = None,
        unterbaufarbe: Annotated[
            int | None,
            typer.Option(help="Farbe des Unterbaus (Rebrickable-ID).", rich_help_panel=SIZE_PANEL),
        ] = None,
        rahmen: Annotated[
            int | None,
            typer.Option(help="Rahmenbreite in Noppen (0 = ohne).", rich_help_panel=FRAME_PANEL),
        ] = None,
        rahmenfarbe: Annotated[
            int | None,
            typer.Option(help="Rahmenfarbe (Rebrickable-ID).", rich_help_panel=FRAME_PANEL),
        ] = None,
        eckenfarbe: Annotated[
            int | None,
            typer.Option(help="Eigene Farbe für die Ecken.", rich_help_panel=FRAME_PANEL),
        ] = None,
        rahmen_teile: Annotated[
            str | None,
            typer.Option(help="Teilarten des Rahmens, z. B. tile.", rich_help_panel=FRAME_PANEL),
        ] = None,
        rahmen_erhoehung: Annotated[
            int | None,
            typer.Option(
                help="Rahmen um so viele Plates erhöhen (0 = bündig).",
                rich_help_panel=FRAME_PANEL,
            ),
        ] = None,
        modus: Annotated[
            str | None,
            typer.Option(help="zuschneiden oder einpassen.", rich_help_panel=IMAGE_PANEL),
        ] = None,
        kontrast: Annotated[
            float | None,
            typer.Option(help="Kontrast (1.0 = unverändert).", rich_help_panel=IMAGE_PANEL),
        ] = None,
        saettigung: Annotated[
            float | None,
            typer.Option(help="Sättigung (1.0 = unverändert).", rich_help_panel=IMAGE_PANEL),
        ] = None,
        teile: Annotated[
            str | None,
            typer.Option(help="Teilarten, z. B. plate,tile.", rich_help_panel=IMAGE_PANEL),
        ] = None,
        farbkategorien: Annotated[
            str | None,
            typer.Option(
                help="Farbkategorien, z. B. solid,metallic,pearl.", rich_help_panel=IMAGE_PANEL
            ),
        ] = None,
        exotische_farben: Annotated[
            bool,
            typer.Option(
                "--exotische-farben",
                help="Alle Kategorien außer Gummi erlauben.",
                rich_help_panel=IMAGE_PANEL,
            ),
        ] = False,
        max_farben: Annotated[
            int | None,
            typer.Option(help="Höchstens so viele Farben (0 = alle).", rich_help_panel=IMAGE_PANEL),
        ] = None,
        dithering: Annotated[
            str | None, typer.Option(help="Dithering-Verfahren.", rich_help_panel=METHOD_PANEL)
        ] = None,
        staerke: Annotated[
            float | None,
            typer.Option(help="Dithering-Stärke von 0.0 bis 1.0.", rich_help_panel=METHOD_PANEL),
        ] = None,
        farbabstand: Annotated[
            str | None,
            typer.Option(help="cie76 (schnell) oder ciede2000.", rich_help_panel=METHOD_PANEL),
        ] = None,
        zerlegung: Annotated[
            str | None, typer.Option(help="Zerlegungsverfahren.", rich_help_panel=METHOD_PANEL)
        ] = None,
        ausgaben: Annotated[
            str | None,
            typer.Option(help="Ausgaben, z. B. preview_png,ldraw.", rich_help_panel=METHOD_PANEL),
        ] = None,
        ziel: Annotated[
            Path | None, typer.Option(help="Ausgabeordner.", rich_help_panel=METHOD_PANEL)
        ] = None,
    ) -> None:
        """Erzeugt ein Mosaik aus BILD (Optionen ohne Angabe kommen aus der Konfiguration)."""
        raw = OverrideInput(
            substrate=unterbau,
            plate_studs=plattengroesse,
            plates=platten,
            width_studs=breite,
            height_studs=hoehe,
            substrate_color_id=unterbaufarbe,
            frame_width=rahmen,
            frame_color_id=rahmenfarbe,
            frame_corner_color_id=eckenfarbe,
            frame_part_kinds=rahmen_teile,
            frame_raise_plates=rahmen_erhoehung,
            fit_mode=modus,
            contrast=kontrast,
            saturation=saettigung,
            part_kinds=teile,
            color_categories=farbkategorien,
            exotic_colors=exotische_farben,
            max_colors=max_farben,
            dithering_method=dithering,
            strength=staerke,
            color_distance=farbabstand,
            tiling_strategy=zerlegung,
            outputs=ausgaben,
            output_dir=ziel,
        )

        def action() -> MosaicResult:
            overrides = parse_overrides(raw)
            pipeline = pipeline_factory(config)
            with console.status("Starte …") as status:
                return pipeline.run(
                    bild, overrides, title=bild.stem, on_step=lambda label: status.update(label)
                )

        print_summary(console, _run_safely(state, console, action))


# --- interaktiv --------------------------------------------------------------------


def _register_interactive(
    app: typer.Typer,
    config: AppConfig,
    pipeline_factory: PipelineFactory,
    state: _State,
    console: Console,
) -> None:
    """Registriert den Befehl ``interaktiv``."""

    @app.command("interaktiv")
    def interactive(
        bild: Annotated[
            Path | None, typer.Argument(help="Optional: Bild, das gleich geladen wird.")
        ] = None,
    ) -> None:
        """Menügeführter Modus: Bild laden, Optionen einstellen, Vorschau, Mosaik ausgeben."""
        session = InteractiveSession(console, config, pipeline_factory, debug=state.debug)
        session.run(bild)


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
            raw = OverrideInput(
                part_kinds=teile, color_categories=farbkategorien, exotic_colors=exotische_farben
            )
            return pipeline_factory(config).palette(parse_overrides(raw))

        print_palette(console, _run_safely(state, console, action))

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
