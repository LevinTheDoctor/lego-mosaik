"""Deutsche Kommandozeile (Typer + Rich).

Die CLI enthält keine Fachlogik: Sie liest Optionen, übersetzt sie in
``MosaicOverrides``, ruft die Pipeline auf und gibt das Ergebnis aus.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated

lazy import typer
lazy from rich.console import Console
lazy from rich.logging import RichHandler
lazy from rich.table import Table

from lego_mosaik.config import (
    AppConfig,
    OverrideInput,
    form_values,
    override_input,
    parse_overrides,
    run_title,
    validate_config,
)
from lego_mosaik.domain.editing import AreaEdit, FillEdit, GridEdit, StudEdit
from lego_mosaik.domain.errors import (
    ConfigError,
    InvalidImageError,
    LegoMosaicError,
    NotFoundError,
)
from lego_mosaik.domain.library import PresetOrder
from lego_mosaik.domain.models import LegoColor
from lego_mosaik.interfaces.display import (
    print_background,
    print_formats,
    print_palette,
    print_palettes,
    print_presets,
    print_prices,
    print_summary,
)
from lego_mosaik.storage.library import Library
lazy from lego_mosaik.interfaces.interactive import InteractiveSession
lazy from lego_mosaik.outputs.result_json import find_mosaic
lazy from lego_mosaik.pipeline import BackgroundResult, MosaicPipeline, MosaicResult
lazy from lego_mosaik.pricing.estimate import PriceEstimate
lazy from lego_mosaik.pricing.service import PriceService
lazy from lego_mosaik.sources.protocol import FileStatus, UpdateReport

# Exit-Codes laut Anforderung
EXIT_PROCESSING_ERROR = 1
EXIT_INVALID_INPUT = 2

type PipelineFactory = Callable[[AppConfig], MosaicPipeline]
type ServeFunction = Callable[[AppConfig, str, int], None]
type PriceFactory = Callable[[AppConfig], PriceService]

# Gruppen in der Hilfe von „erstellen“ (rich_help_panel), damit die vielen Optionen lesbar bleiben
SIZE_PANEL = "Größe und Unterbau"
FRAME_PANEL = "Rahmen"
IMAGE_PANEL = "Bild bearbeiten"
BACKGROUND_PANEL = "Hintergrund"
COLOR_PANEL = "Farben, Paletten und Presets"
STYLE_PANEL = "Stil und Relief"
METHOD_PANEL = "Verfahren und Ausgaben"


@dataclass(slots=True)
class _State:
    """Globale Optionen (z. B. ``--debug``), die der Callback für alle Befehle setzt."""

    debug: bool = False


def create_cli(
    config: AppConfig,
    pipeline_factory: PipelineFactory,
    serve: ServeFunction | None = None,
    price_factory: PriceFactory | None = None,
) -> typer.Typer:
    """Baut die Typer-App (Factory). Konfiguration und Pipeline kommen von außen (``main.py``).

    Args:
        config: Standardkonfiguration.
        pipeline_factory: Erzeugt die Pipeline (erst bei Bedarf, damit ``--hilfe`` schnell bleibt).
        serve: Startet den HTTP-Server; ``None`` blendet den Befehl ``server`` aus.
        price_factory: Erzeugt die BrickLink-Preisschätzung; ``None`` blendet ``preise`` aus.
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

    _register_create(app, config, pipeline_factory, price_factory, state, console)
    _register_interactive(app, config, pipeline_factory, price_factory, state, console)
    _register_catalog_commands(app, config, pipeline_factory, state, console)
    _register_image_commands(app, config, pipeline_factory, state, console)
    _register_library_commands(app, config, pipeline_factory, state, console)
    if price_factory is not None:
        _register_prices(app, config, price_factory, state, console)
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
        invalid_input = isinstance(error, (ConfigError, InvalidImageError, NotFoundError))
        raise typer.Exit(EXIT_INVALID_INPUT if invalid_input else EXIT_PROCESSING_ERROR) from None


# --- erstellen ---------------------------------------------------------------------


def _register_create(
    app: typer.Typer,
    config: AppConfig,
    pipeline_factory: PipelineFactory,
    price_factory: PriceFactory | None,
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
                help="art (LEGO-Art-Paneele mit Pins), platten (große Platten, mit Plates "
                "verbunden), grundplatte (lose) oder ohne.",
                rich_help_panel=SIZE_PANEL,
            ),
        ] = None,
        plattengroesse: Annotated[
            int | None,
            typer.Option(
                help="Plattengröße in Noppen (Art-Paneel 16; platten und grundplatte 16, 32, 48).",
                rich_help_panel=SIZE_PANEL,
            ),
        ] = None,
        format_name: Annotated[
            str | None,
            typer.Option(
                "--format",
                help="Format, z. B. quadrat (3×3 Paneele), quer, weltkarte (siehe „formate“).",
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
                help="Eigene Breite in Noppen; rastet auf ganze Platten ein.",
                rich_help_panel=SIZE_PANEL,
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
            typer.Option(help="zuschneiden oder einpassen.", rich_help_panel=SIZE_PANEL),
        ] = None,
        kontrast: Annotated[
            float | None,
            typer.Option(help="Kontrast (1.0 = unverändert).", rich_help_panel=IMAGE_PANEL),
        ] = None,
        saettigung: Annotated[
            float | None,
            typer.Option(help="Sättigung (1.0 = unverändert).", rich_help_panel=IMAGE_PANEL),
        ] = None,
        helligkeit: Annotated[
            float | None,
            typer.Option(help="Helligkeit (1.0 = unverändert).", rich_help_panel=IMAGE_PANEL),
        ] = None,
        farbfilter: Annotated[
            str | None,
            typer.Option(
                help="ohne, graustufen, sepia, warm, kalt, invertieren, poster oder vintage.",
                rich_help_panel=IMAGE_PANEL,
            ),
        ] = None,
        filter_staerke: Annotated[
            float | None,
            typer.Option(
                help="Stärke des Farbfilters von 0.0 bis 1.0.", rich_help_panel=IMAGE_PANEL
            ),
        ] = None,
        drehen: Annotated[
            int | None,
            typer.Option(
                help="Im Uhrzeigersinn drehen: 90, 180 oder 270.", rich_help_panel=IMAGE_PANEL
            ),
        ] = None,
        spiegeln: Annotated[
            bool | None,
            typer.Option(
                "--spiegeln/--nicht-spiegeln",
                help="Waagerecht spiegeln.",
                rich_help_panel=IMAGE_PANEL,
            ),
        ] = None,
        ausschnitt: Annotated[
            str | None,
            typer.Option(
                help="Ausschnitt x,y,Breite,Höhe in Prozent, z. B. 10,0,80,100.",
                rich_help_panel=IMAGE_PANEL,
            ),
        ] = None,
        hintergrund: Annotated[
            str | None,
            typer.Option(
                help="behalten, entfernen (einfarbig), aufhellen, abdunkeln, entsaettigen "
                "oder weichzeichnen.",
                rich_help_panel=BACKGROUND_PANEL,
            ),
        ] = None,
        hintergrundfarbe: Annotated[
            int | None,
            typer.Option(
                help="Neue Farbe bei „entfernen“ (Rebrickable-ID; Standard: Randfarbe White).",
                rich_help_panel=BACKGROUND_PANEL,
            ),
        ] = None,
        hintergrund_toleranz: Annotated[
            float | None,
            typer.Option(
                help="So ähnlich (ΔE) muss ein Pixel der Randfarbe sein (Standard 12).",
                rich_help_panel=BACKGROUND_PANEL,
            ),
        ] = None,
        hintergrund_staerke: Annotated[
            float | None,
            typer.Option(
                help="Stärke für aufhellen, abdunkeln, entsättigen, weichzeichnen (0.0–1.0).",
                rich_help_panel=BACKGROUND_PANEL,
            ),
        ] = None,
        teile: Annotated[
            str | None,
            typer.Option(help="Teilarten, z. B. plate,tile.", rich_help_panel=COLOR_PANEL),
        ] = None,
        farbkategorien: Annotated[
            str | None,
            typer.Option(
                help="Farbkategorien, z. B. solid,metallic,pearl.", rich_help_panel=COLOR_PANEL
            ),
        ] = None,
        exotische_farben: Annotated[
            bool,
            typer.Option(
                "--exotische-farben",
                help="Alle Kategorien außer Gummi erlauben.",
                rich_help_panel=COLOR_PANEL,
            ),
        ] = False,
        max_farben: Annotated[
            int | None,
            typer.Option(help="Höchstens so viele Farben (0 = alle).", rich_help_panel=COLOR_PANEL),
        ] = None,
        farb_ids: Annotated[
            str | None,
            typer.Option(
                help="Nur diese Farben (Rebrickable-IDs), z. B. 0,15,71.",
                rich_help_panel=COLOR_PANEL,
            ),
        ] = None,
        palette: Annotated[
            str | None,
            typer.Option(help="Eigene Palette (siehe „paletten“).", rich_help_panel=COLOR_PANEL),
        ] = None,
        preset: Annotated[
            str | None,
            typer.Option(
                help="Preset als Grundlage (siehe „presets“); angegebene Optionen gehen vor.",
                rich_help_panel=COLOR_PANEL,
            ),
        ] = None,
        stil: Annotated[
            str | None,
            typer.Option(
                help="quadrat, formen (Rundungen und Schrägen an Kanten), punkte "
                "(runde Tiles) oder noppen (runde Plates).",
                rich_help_panel=STYLE_PANEL,
            ),
        ] = None,
        relief: Annotated[
            str | None,
            typer.Option(
                help="ohne, motiv (Hintergrund flach, Motiv erhöht), hell oder dunkel "
                "(helle bzw. dunkle Farben höher).",
                rich_help_panel=STYLE_PANEL,
            ),
        ] = None,
        relief_hoehe: Annotated[
            int | None,
            typer.Option(help="Höchste Relief-Stufe in Plates (1–3).", rich_help_panel=STYLE_PANEL),
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
        aufraeumen: Annotated[
            bool | None,
            typer.Option(
                "--aufraeumen/--nicht-aufraeumen",
                help="Einzelne Pixel durch die Nachbarfarbe ersetzen: weniger Rauschen und "
                "1×1-Teile (gut für Comics und Logos).",
                rich_help_panel=METHOD_PANEL,
            ),
        ] = None,
        glaettung: Annotated[
            str | None,
            typer.Option(
                help="Glättung: aus, leicht, mittel (Mehrheitsfarbe je Noppe, klare Flächen "
                "für Logos und Comics) oder stark. Kontrastreiche Details wie Augen bleiben.",
                rich_help_panel=METHOD_PANEL,
            ),
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
        titel: Annotated[
            str | None,
            typer.Option(
                help="Name des Mosaiks auf dem Titelblatt (Standard: Name der Bilddatei).",
                rich_help_panel=METHOD_PANEL,
            ),
        ] = None,
        preise: Annotated[
            bool,
            typer.Option(
                "--preise",
                help="Danach den Preis bei BrickLink schätzen (braucht Zugangsdaten).",
                rich_help_panel=METHOD_PANEL,
            ),
        ] = False,
    ) -> None:
        """Erzeugt ein Mosaik aus BILD (Optionen ohne Angabe kommen aus der Konfiguration)."""
        values = {
            "unterbau": unterbau,
            "plattengroesse": plattengroesse,
            "format": format_name,
            "platten": platten,
            "breite": breite,
            "hoehe": hoehe,
            "unterbaufarbe": unterbaufarbe,
            "rahmen": rahmen,
            "rahmenfarbe": rahmenfarbe,
            "eckenfarbe": eckenfarbe,
            "rahmen_teile": rahmen_teile,
            "rahmen_erhoehung": rahmen_erhoehung,
            "modus": modus,
            "kontrast": kontrast,
            "saettigung": saettigung,
            "helligkeit": helligkeit,
            "farbfilter": farbfilter,
            "filter_staerke": filter_staerke,
            "drehen": drehen,
            "spiegeln": spiegeln,
            "ausschnitt": ausschnitt,
            "hintergrund": hintergrund,
            "hintergrundfarbe": hintergrundfarbe,
            "hintergrund_toleranz": hintergrund_toleranz,
            "hintergrund_staerke": hintergrund_staerke,
            "teile": teile,
            "farbkategorien": farbkategorien,
            "exotische_farben": exotische_farben or None,  # False = nicht angegeben
            "max_farben": max_farben,
            "farb_ids": farb_ids,
            "stil": stil,
            "relief": relief,
            "relief_hoehe": relief_hoehe,
            "dithering": dithering,
            "staerke": staerke,
            "farbabstand": farbabstand,
            "aufraeumen": aufraeumen,
            "glaettung": glaettung,
            "zerlegung": zerlegung,
            "ausgaben": ausgaben,
            "titel": titel,
        }

        def action() -> MosaicResult:
            library = Library.open(config.storage)
            resolved = library.resolve(values, preset=preset, palette=palette)
            raw = replace(resolved.inputs, output_dir=ziel)
            pipeline = pipeline_factory(config)
            with console.status("Starte …") as status:
                result = pipeline.run(
                    bild,
                    parse_overrides(raw),
                    title=run_title(raw, bild.stem),
                    on_step=lambda label: status.update(label),
                    inputs=raw,
                )
            if resolved.preset is not None:
                library.presets.record_use(resolved.preset.id)
            return result

        result = _run_safely(state, console, action)
        print_summary(console, result)
        if preise:
            if price_factory is None:
                raise typer.BadParameter(
                    "Preisschätzung ist nicht eingerichtet.", param_hint="--preise"
                )
            _estimate_and_print(state, console, config, price_factory, result.target_dir)


# --- interaktiv --------------------------------------------------------------------


def _register_interactive(
    app: typer.Typer,
    config: AppConfig,
    pipeline_factory: PipelineFactory,
    price_factory: PriceFactory | None,
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
        session = InteractiveSession(
            console, config, pipeline_factory, price_factory=price_factory, debug=state.debug
        )
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

    @app.command("formate")
    def formats() -> None:
        """Listet die Formate (Raster aus Paneelen bzw. Grundplatten) auf."""
        print_formats(console, config.layout.plate_studs)

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


# --- Bild: Hintergrund und Bearbeiten -------------------------------------------------


def _register_image_commands(
    app: typer.Typer,
    config: AppConfig,
    pipeline_factory: PipelineFactory,
    state: _State,
    console: Console,
) -> None:
    """Registriert ``hintergrund`` und ``bearbeiten``."""

    @app.command("hintergrund")
    def background(
        bild: Annotated[Path, typer.Argument(help="Bilddatei (JPG, PNG, …).")],
        toleranz: Annotated[
            float | None,
            typer.Option(help="So ähnlich (ΔE) muss ein Pixel der Randfarbe sein (Standard 12)."),
        ] = None,
        drehen: Annotated[int | None, typer.Option(help="Vorher drehen: 90, 180, 270.")] = None,
        ausschnitt: Annotated[
            str | None, typer.Option(help="Vorher zuschneiden: x,y,Breite,Höhe in Prozent.")
        ] = None,
        maske: Annotated[
            Path | None, typer.Option(help="Maske als PNG speichern (weiß = Hintergrund).")
        ] = None,
    ) -> None:
        """Erkennt den Hintergrund eines Bilds und schlägt eine LEGO-Farbe vor."""
        values = {"hintergrund_toleranz": toleranz, "drehen": drehen, "ausschnitt": ausschnitt}

        def action() -> BackgroundResult:
            overrides = parse_overrides(override_input(values))
            return pipeline_factory(config).detect_background(bild, overrides, max_px=1024)

        result = _run_safely(state, console, action)
        print_background(console, result)
        if maske is not None:
            maske.write_bytes(result.report.mask_png)
            console.print(f"Maske gespeichert: {maske}")

    @app.command("bearbeiten")
    def edit(
        mosaik: Annotated[
            str | None,
            typer.Argument(help="ID (Ordnername in ausgabe/) oder Ordner; Standard: das neueste."),
        ] = None,
        noppe: Annotated[
            list[str] | None,
            typer.Option(help="Noppe umfärben: ZEILE,SPALTE=FARBE, z. B. 5,7=15 (mehrfach)."),
        ] = None,
        flaeche: Annotated[
            list[str] | None,
            typer.Option(
                help="Rechteck umfärben: ZEILE,SPALTE,BREITE,HOEHE=FARBE, z. B. 2,2,4,3=0."
            ),
        ] = None,
        fuellen: Annotated[
            list[str] | None,
            typer.Option(help="Gleichfarbige Fläche umfärben (Farbeimer): ZEILE,SPALTE=FARBE."),
        ] = None,
    ) -> None:
        """Färbt Noppen oder Flächen eines Mosaiks um und rechnet es neu (neuer Ordner).

        Zeilen und Spalten zählen ab 1 mit Rahmen, wie in Vorschau und Anleitung.
        """

        def action() -> MosaicResult:
            edits = _parse_edits(noppe or [], flaeche or [], fuellen or [])
            directory = find_mosaic(config.output_dir, mosaik)
            with console.status("Rechne neu …") as status:
                return pipeline_factory(config).edit(
                    directory, edits, on_step=lambda label: status.update(label)
                )

        print_summary(console, _run_safely(state, console, action))


def _parse_edits(studs: list[str], areas: list[str], fills: list[str]) -> list[GridEdit]:
    """``"5,7=15"`` bzw. ``"2,2,4,3=0"`` → Änderungen (Reihenfolge: Noppen, Flächen, Füllen).

    Raises:
        ConfigError: Bei falschem Format oder ohne Änderung.
    """
    edits: list[GridEdit] = []
    for text in studs:
        (row, col), color = _edit_numbers(text, 2)
        edits.append(StudEdit(row, col, color))
    for text in areas:
        (row, col, width, height), color = _edit_numbers(text, 4)
        edits.append(AreaEdit(row, col, width, height, color))
    for text in fills:
        (row, col), color = _edit_numbers(text, 2)
        edits.append(FillEdit(row, col, color))
    if not edits:
        raise ConfigError("Bitte mindestens eine Änderung angeben (--noppe, --flaeche, --fuellen).")
    return edits


def _edit_numbers(text: str, count: int) -> tuple[list[int], int]:
    position, separator, color = text.partition("=")
    numbers = [part.strip() for part in position.split(",")]
    if not separator or len(numbers) != count or not all(n.isdigit() for n in numbers):
        raise ConfigError(f"Änderung „{text}“ hat nicht das Format ZEILE,SPALTE…=FARBE.")
    if not color.strip().isdigit():
        raise ConfigError(f"Änderung „{text}“: Die Farbe muss eine Rebrickable-ID sein.")
    return [int(n) for n in numbers], int(color)


# --- Paletten und Presets -------------------------------------------------------------


def _register_library_commands(
    app: typer.Typer,
    config: AppConfig,
    pipeline_factory: PipelineFactory,
    state: _State,
    console: Console,
) -> None:
    """Registriert die Befehlsgruppen ``paletten`` und ``presets``."""
    palettes_app = typer.Typer(help="Eigene Farbpaletten: anzeigen, anlegen, löschen.")
    presets_app = typer.Typer(help="Presets: anzeigen, speichern, bewerten, Favoriten, löschen.")
    app.add_typer(palettes_app, name="paletten")
    app.add_typer(presets_app, name="presets")

    def library() -> Library:
        return Library.open(config.storage)

    @palettes_app.callback(invoke_without_command=True)
    def list_palettes(context: typer.Context) -> None:
        """Ohne Unterbefehl: alle eigenen Paletten mit Farbfeldern."""
        if context.invoked_subcommand is None:
            palettes = _run_safely(state, console, lambda: library().palettes.list())
            colors = _run_safely(state, console, lambda: pipeline_factory(config).load_catalog())
            print_palettes(console, palettes, colors.colors)

    @palettes_app.command("anlegen")
    def create_palette(
        name: Annotated[str, typer.Argument(help="Name der Palette.")],
        farben: Annotated[str, typer.Option(help="Rebrickable-IDs, z. B. 0,15,71,72.")],
        beschreibung: Annotated[str, typer.Option(help="Freitext.")] = "",
    ) -> None:
        """Legt eine Palette an (Farb-IDs siehe „farben“)."""

        def action() -> str:
            ids = parse_overrides(OverrideInput(color_ids=farben)).only_color_ids or ()
            pipeline_factory(config).colors(ids)
            return library().palettes.create(name, beschreibung, ids).id

        palette_id = _run_safely(state, console, action)
        console.print(f"[green]Palette angelegt:[/green] {palette_id} (nutzen mit --palette)")

    @palettes_app.command("loeschen")
    def delete_palette(name: Annotated[str, typer.Argument(help="Name oder ID.")]) -> None:
        """Löscht eine Palette."""
        _run_safely(state, console, lambda: library().palettes.delete(name))
        console.print("[green]Gelöscht.[/green]")

    @presets_app.callback(invoke_without_command=True)
    def list_presets(
        context: typer.Context,
        sortierung: Annotated[
            PresetOrder, typer.Option(help="bewertung, nutzung, name oder neu.")
        ] = PresetOrder.RATING,
        favoriten: Annotated[bool, typer.Option("--favoriten", help="Nur Favoriten.")] = False,
    ) -> None:
        """Ohne Unterbefehl: alle Presets, Favoriten zuerst."""
        if context.invoked_subcommand is None:
            presets = _run_safely(
                state, console, lambda: library().presets.list(sortierung, favorites_only=favoriten)
            )
            print_presets(console, presets)

    @presets_app.command("speichern")
    def save_preset(
        name: Annotated[str, typer.Argument(help="Name des Presets.")],
        mosaik: Annotated[
            str | None,
            typer.Argument(
                help="Einstellungen dieses Mosaiks (ID oder Ordner); Standard: neuestes."
            ),
        ] = None,
        beschreibung: Annotated[str, typer.Option(help="Freitext.")] = "",
        favorit: Annotated[bool, typer.Option("--favorit", help="Als Favorit markieren.")] = False,
        bewertung: Annotated[int, typer.Option(help="0–5 Sterne.")] = 0,
    ) -> None:
        """Speichert die Einstellungen eines fertigen Mosaiks als Preset."""

        def action() -> str:
            pipeline = pipeline_factory(config)
            values = form_values(pipeline.inputs_of(find_mosaic(config.output_dir, mosaik)))
            values.pop("titel", None)
            lib = library()
            settings = lib.preset_settings(values, pipeline.check)
            preset = lib.presets.create(
                name, beschreibung, settings, favorite=favorit, rating=bewertung
            )
            return preset.id

        preset_id = _run_safely(state, console, action)
        console.print(f"[green]Preset gespeichert:[/green] {preset_id} (nutzen mit --preset)")

    @presets_app.command("favorit")
    def favorite(
        name: Annotated[str, typer.Argument(help="Name oder ID.")],
        aus: Annotated[bool, typer.Option("--aus", help="Favorit entfernen.")] = False,
    ) -> None:
        """Markiert ein Preset als Favorit (mit --aus: nicht mehr)."""
        _run_safely(state, console, lambda: library().presets.mark(name, favorite=not aus))
        console.print("[green]Gespeichert.[/green]")

    @presets_app.command("bewerten")
    def rate(
        name: Annotated[str, typer.Argument(help="Name oder ID.")],
        sterne: Annotated[int, typer.Argument(help="0–5 Sterne (0 = unbewertet).")],
    ) -> None:
        """Bewertet ein Preset."""
        _run_safely(state, console, lambda: library().presets.mark(name, rating=sterne))
        console.print("[green]Gespeichert.[/green]")

    @presets_app.command("loeschen")
    def delete_preset(name: Annotated[str, typer.Argument(help="Name oder ID.")]) -> None:
        """Löscht ein Preset."""
        _run_safely(state, console, lambda: library().presets.delete(name))
        console.print("[green]Gelöscht.[/green]")


# --- preise ---------------------------------------------------------------------------


def _register_prices(
    app: typer.Typer,
    config: AppConfig,
    price_factory: PriceFactory,
    state: _State,
    console: Console,
) -> None:
    """Registriert ``preise``."""

    @app.command("preise")
    def prices(
        mosaik: Annotated[
            str | None,
            typer.Argument(help="ID (Ordnername in ausgabe/) oder Ordner; Standard: das neueste."),
        ] = None,
        offline: Annotated[
            bool, typer.Option("--offline", help="Nur gespeicherte Preise, keine Abfrage.")
        ] = False,
        erzwingen: Annotated[
            bool, typer.Option("--erzwingen", help="Alle Preise neu abfragen, auch aktuelle.")
        ] = False,
    ) -> None:
        """Schätzt den Preis eines Mosaiks bei BrickLink und aktualisiert die Wanted List."""
        directory = _run_safely(state, console, lambda: price_factory(config).find_mosaic(mosaik))
        _estimate_and_print(
            state, console, config, price_factory, directory, offline=offline, force=erzwingen
        )


def _estimate_and_print(
    state: _State,
    console: Console,
    config: AppConfig,
    price_factory: PriceFactory,
    directory: Path,
    *,
    offline: bool = False,
    force: bool = False,
) -> None:
    """Preisschätzung mit Fortschrittsanzeige und Tabelle (für ``preise`` und ``--preise``)."""
    service = _run_safely(state, console, lambda: price_factory(config))

    def action() -> PriceEstimate:
        with console.status("Frage Preise bei BrickLink ab …"):
            return service.estimate(directory, offline=offline, force=force)

    print_prices(console, _run_safely(state, console, action), service.pricing.upload_url)


# --- server ----------------------------------------------------------------------------


def _register_server(
    app: typer.Typer, config: AppConfig, serve: ServeFunction, state: _State
) -> None:
    """Registriert ``server``."""

    @app.command("server")
    def server(
        host: Annotated[str | None, typer.Option(help="Adresse (Standard: 127.0.0.1).")] = None,
        port: Annotated[int | None, typer.Option(help="Port (Standard: 8000).")] = None,
        cors: Annotated[
            list[str] | None,
            typer.Option(
                help="Weitere Webseite, die die API aufrufen darf, z. B. http://192.168.1.5:5173 "
                "(mehrfach möglich; localhost ist immer erlaubt)."
            ),
        ] = None,
    ) -> None:
        """Startet die lokale HTTP-API (FastAPI)."""
        server_config = config
        if cors:
            origins = (*config.server.cors_origins, *(origin.rstrip("/") for origin in cors))
            server_config = replace(config, server=replace(config.server, cors_origins=origins))
            try:
                validate_config(server_config)
            except ConfigError as error:
                raise typer.BadParameter(str(error), param_hint="--cors") from None
        serve(server_config, host or config.server.host, port or config.server.port)
