"""Interaktiver Modus: menügeführte Bedienung im Terminal (Rich-Prompts).

Ablauf: Bild laden → Optionen einstellen → Vorschau im Terminal ansehen → Mosaik
mit allen Dateien ausgeben. Alle Eingaben landen in ``OverrideInput``, genau wie bei
CLI und API. Geprüft wird deshalb an derselben Stelle (``apply_overrides``).
"""

import functools
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

lazy from rich.console import Console
lazy from rich.markup import escape
lazy from rich.prompt import Prompt
lazy from rich.table import Table

from lego_mosaik.config import (
    BACKGROUND_LABELS,
    COLOR_DISTANCE_LABELS,
    COLOR_FILTER_LABELS,
    FIT_MODE_LABELS,
    RELIEF_LABELS,
    SMOOTHING_LABELS,
    SUBSTRATE_LABELS,
    AppConfig,
    OverrideInput,
    apply_overrides,
    form_values,
    frame_part_kinds,
    label_of,
    parse_overrides,
    run_title,
    substrate_color_id,
)
from lego_mosaik.domain.errors import LegoMosaicError
from lego_mosaik.domain.layout import FORMATS, suggest_formats
from lego_mosaik.interfaces.display import (
    mosaic_preview,
    print_formats,
    print_palette,
    print_presets,
    print_prices,
    print_summary,
)
from lego_mosaik.storage.library import Library
lazy from lego_mosaik.imaging.protocol import ImageInfo
lazy from lego_mosaik.pipeline import MosaicPipeline
lazy from lego_mosaik.pricing.service import PriceService

logger = logging.getLogger(__name__)

RESET = "-"  # Eingabe, die einen Wert auf den Standard aus der Konfiguration zurücksetzt
EXIT_CHOICES = frozenset({"0", "q", "beenden"})
YES = frozenset({"j", "ja", "y", "yes"})
NO = frozenset({"n", "nein", "no"})
# Format und eigene Größe schließen sich aus: Wer das eine ändert, setzt das andere zurück
FORMAT_FIELD = "art_format"
SIZE_FIELDS = ("plates", "width_studs", "height_studs")

type PipelineFactory = Callable[[AppConfig], MosaicPipeline]
type PriceFactory = Callable[[AppConfig], PriceService]
type Convert = Callable[[str], object]
type Current = Callable[[AppConfig, OverrideInput], object]


@dataclass(frozen=True, slots=True)
class _Field:
    """Eine Frage im Menü.

    Attributes:
        name: Feldname in ``OverrideInput``.
        question: Deutsche Frage.
        convert: Wandelt die Texteingabe um (z. B. ``int``); ``ValueError`` = ungültig.
        current: Liefert den aktuell wirksamen Wert zur Anzeige.
    """

    name: str
    question: str
    convert: Convert
    current: Current


def _yes_no(text: str) -> bool:
    """„ja“/„nein“ (auch j/n, yes/no) → bool."""
    answer = text.strip().lower()
    if answer in YES | NO:  # | vereinigt zwei Mengen
        return answer in YES
    raise ValueError(text)


def _or_dash(value: object) -> object:
    """Leere Werte als „–“ anzeigen."""
    return "–" if value is None else value


def _crop_text(config: AppConfig) -> str:
    crop = config.mosaic.crop
    return "ganzes Bild" if crop is None else ",".join(f"{value:g}" for value in crop)


def _format_name(config: AppConfig) -> str:
    """Name des Formats, das zur aktuellen Plattenzahl passt (sonst „–“)."""
    layout = config.layout
    if layout.custom_size:
        return "–"
    grid = (layout.plates_wide, layout.plates_high)
    return next((f.name for f in FORMATS if (f.plates_wide, f.plates_high) == grid), "–")


# Menügruppen: Titel → Fragen. Die lambdas lesen den aktuell wirksamen Wert aus der
# Laufkonfiguration (c) bzw. aus den Eingaben (i), damit Enter „beibehalten“ heißt.
GROUPS: tuple[tuple[str, tuple[_Field, ...]], ...] = (
    (
        "Größe und Unterbau",
        (
            _Field(
                "substrate",
                "Unterbau: art (Paneele mit Pins), platten (große Platten, verbunden), "
                "grundplatte (lose) oder ohne",
                str,
                lambda c, i: label_of(SUBSTRATE_LABELS, c.layout.substrate),
            ),
            _Field(
                "plate_studs",
                "Plattengröße in Noppen (Art-Paneel 16; platten/grundplatte 16, 32 oder 48)",
                int,
                lambda c, i: c.layout.plate_studs,
            ),
            _Field(
                FORMAT_FIELD,
                "Format, z. B. quadrat, quer, weltkarte (statt Platten)",
                str,
                lambda c, i: _format_name(c),
            ),
            _Field(
                "plates",
                "Anzahl Platten als BxH, z. B. 3x3",
                str,
                lambda c, i: f"{c.layout.plates_wide}x{c.layout.plates_high}",
            ),
            _Field(
                "width_studs",
                "Eigene Breite in Noppen (rastet auf ganze Platten ein)",
                int,
                lambda c, i: _or_dash(c.layout.width_studs),
            ),
            _Field(
                "height_studs",
                "Eigene Höhe in Noppen (leer lassen = aus dem Seitenverhältnis)",
                int,
                lambda c, i: _or_dash(c.layout.height_studs),
            ),
            _Field(
                "substrate_color_id",
                "Farbe des Unterbaus (Rebrickable-ID)",
                int,
                lambda c, i: substrate_color_id(c.layout),
            ),
        ),
    ),
    (
        "Rahmen",
        (
            _Field(
                "frame_width", "Rahmenbreite in Noppen (0 = ohne)", int, lambda c, i: c.frame.width
            ),
            _Field(
                "frame_color_id", "Rahmenfarbe (Rebrickable-ID)", int, lambda c, i: c.frame.color_id
            ),
            _Field(
                "frame_corner_color_id",
                "Eckenfarbe (Rebrickable-ID, „-“ = wie der Rahmen)",
                int,
                lambda c, i: _or_dash(c.frame.corner_color_id),
            ),
            _Field(
                "frame_part_kinds",
                "Teilarten des Rahmens (z. B. tile für glatt, plate,tile)",
                str,
                lambda c, i: ",".join(frame_part_kinds(c)),
            ),
            _Field(
                "frame_raise_plates",
                "Erhöhung des Rahmens in Plates (0 = bündig, 1 = wie LEGO Art)",
                int,
                lambda c, i: c.frame.raise_plates,
            ),
        ),
    ),
    (
        "Bild anpassen",
        (
            _Field(
                "fit_mode",
                "Modus: zuschneiden oder einpassen",
                str,
                lambda c, i: label_of(FIT_MODE_LABELS, c.mosaic.fit_mode),
            ),
            _Field(
                "contrast", "Kontrast (1.0 = unverändert)", float, lambda c, i: c.mosaic.contrast
            ),
            _Field(
                "saturation",
                "Sättigung (1.0 = unverändert, 0 = Graustufen)",
                float,
                lambda c, i: c.mosaic.saturation,
            ),
            _Field(
                "brightness",
                "Helligkeit (1.0 = unverändert)",
                float,
                lambda c, i: c.mosaic.brightness,
            ),
            _Field(
                "rotate",
                "Drehen im Uhrzeigersinn (0, 90, 180, 270)",
                int,
                lambda c, i: c.mosaic.rotate,
            ),
            _Field(
                "mirror",
                "Waagerecht spiegeln? (ja/nein)",
                _yes_no,
                lambda c, i: "ja" if c.mosaic.mirror else "nein",
            ),
            _Field(
                "crop",
                "Ausschnitt x,y,Breite,Höhe in Prozent (z. B. 10,0,80,100)",
                str,
                lambda c, i: _crop_text(c),
            ),
        ),
    ),
    (
        "Farbfilter und Hintergrund",
        (
            _Field(
                "color_filter",
                f"Farbfilter: {', '.join(COLOR_FILTER_LABELS)}",
                str,
                lambda c, i: label_of(COLOR_FILTER_LABELS, c.mosaic.color_filter),
            ),
            _Field(
                "filter_strength",
                "Stärke des Filters (0.0 bis 1.0)",
                float,
                lambda c, i: c.mosaic.filter_strength,
            ),
            _Field(
                "background",
                f"Hintergrund: {', '.join(BACKGROUND_LABELS)}",
                str,
                lambda c, i: label_of(BACKGROUND_LABELS, c.mosaic.background),
            ),
            _Field(
                "background_color_id",
                "Neue Hintergrundfarbe bei „entfernen“ (Rebrickable-ID, „-“ = Randfarbe)",
                int,
                lambda c, i: _or_dash(c.mosaic.background_color_id),
            ),
            _Field(
                "background_tolerance",
                "Toleranz der Erkennung (ΔE, größer = mehr Hintergrund)",
                float,
                lambda c, i: c.mosaic.background_tolerance,
            ),
            _Field(
                "background_amount",
                "Stärke für aufhellen, abdunkeln, entsättigen, weichzeichnen (0.0 bis 1.0)",
                float,
                lambda c, i: c.mosaic.background_amount,
            ),
        ),
    ),
    (
        "Farben und Teile",
        (
            _Field(
                "part_kinds",
                "Teilarten des Bilds (z. B. plate,tile oder brick)",
                str,
                lambda c, i: ",".join(c.palette.part_kinds),
            ),
            _Field(
                "color_categories",
                "Farbkategorien (z. B. solid,pearl,metallic)",
                str,
                lambda c, i: ",".join(c.palette.color_categories),
            ),
            _Field(
                "exotic_colors",
                "Alle exotischen Farben dazunehmen? (ja/nein)",
                _yes_no,
                lambda c, i: "ja" if i.exotic_colors else "nein",
            ),
            _Field(
                "max_colors",
                "Höchstens so viele Farben (0 = unbegrenzt)",
                int,
                lambda c, i: c.palette.max_colors or "unbegrenzt",
            ),
            _Field(
                "color_ids",
                "Nur diese Farb-IDs, z. B. 0,15,71 („-“ = alle; Paletten über „Preset laden“)",
                str,
                lambda c, i: i.color_ids or "alle",
            ),
        ),
    ),
    (
        "Stil und Relief",
        (
            _Field(
                "style",
                "Stil: quadrat, formen (Rundungen/Schrägen), punkte (runde Tiles), noppen",
                str,
                lambda c, i: c.shapes.style,
            ),
            _Field(
                "relief",
                "Relief: ohne, motiv (Motiv erhöht), hell oder dunkel (höher)",
                str,
                lambda c, i: label_of(RELIEF_LABELS, c.relief.mode),
            ),
            _Field(
                "relief_levels",
                "Höchste Relief-Stufe in Plates (1–3)",
                int,
                lambda c, i: c.relief.levels,
            ),
        ),
    ),
    (
        "Verfahren",
        (
            _Field("dithering_method", "Dithering-Verfahren", str, lambda c, i: c.dithering.method),
            _Field(
                "strength",
                "Dithering-Stärke (0.0 bis 1.0)",
                float,
                lambda c, i: c.dithering.strength,
            ),
            _Field(
                "despeckle",
                "Einzelne Pixel aufräumen (ja/nein; gut für Comics, weniger für Fotos)",
                _yes_no,
                lambda c, i: "ja" if c.dithering.despeckle else "nein",
            ),
            _Field(
                "smoothing",
                "Glättung: aus, leicht, mittel (klare Flächen) oder stark",
                str,
                lambda c, i: label_of(SMOOTHING_LABELS, c.dithering.smoothing),
            ),
            _Field(
                "color_distance",
                "Farbabstand: cie76 (schnell) oder ciede2000 (genau)",
                str,
                lambda c, i: label_of(COLOR_DISTANCE_LABELS, c.dithering.color_distance),
            ),
            _Field("tiling_strategy", "Zerlegung", str, lambda c, i: c.tiling.strategy),
        ),
    ),
    (
        "Name und Ausgaben",
        (
            _Field(
                "title",
                "Name des Mosaiks auf dem Titelblatt („-“ = wie die Bilddatei)",
                str,
                lambda c, i: i.title or "wie die Bilddatei",
            ),
            _Field(
                "outputs",
                "Ausgaben (kommagetrennt, z. B. preview_png,ldraw)",
                str,
                lambda c, i: ",".join(c.outputs),
            ),
            _Field(
                "output_dir",
                "Zielordner",
                lambda text: Path(text).expanduser(),
                lambda c, i: c.output_dir,
            ),
        ),
    ),
)


class InteractiveSession:
    """Hält Bild und Eingaben zwischen den Menüaktionen.

    Args:
        console: Rich-Konsole für Ein- und Ausgabe.
        config: Standardkonfiguration aus ``main.py``.
        pipeline_factory: Erzeugt die Pipeline (einmal, beim ersten Bedarf).
        price_factory: Erzeugt die BrickLink-Preisschätzung; ``None`` blendet den Menüpunkt aus.
        debug: Fehler mit Traceback weiterwerfen statt nur die Meldung zu zeigen.
    """

    def __init__(
        self,
        console: Console,
        config: AppConfig,
        pipeline_factory: PipelineFactory,
        *,
        price_factory: PriceFactory | None = None,
        debug: bool = False,
    ) -> None:
        self.console = console
        self.config = config
        self.inputs = OverrideInput()
        self.image: Path | None = None
        self.image_info: ImageInfo | None = None
        self._pipeline_factory = pipeline_factory
        self._pipeline: MosaicPipeline | None = None
        self._price_factory = price_factory
        self._last_result_dir: Path | None = None  # zuletzt erstelltes Mosaik (für Preise)
        self._debug = debug

    @property
    def pipeline(self) -> MosaicPipeline:
        """Die Pipeline; erst beim ersten Zugriff gebaut (Lazy Initialization)."""
        if self._pipeline is None:
            self._pipeline = self._pipeline_factory(self.config)
        return self._pipeline

    def run_config(self) -> AppConfig:
        """Konfiguration mit allen bisherigen Eingaben (bereits geprüft)."""
        return apply_overrides(self.config, parse_overrides(self.inputs))

    # --- Hauptschleife -----------------------------------------------------------

    def run(self, image: Path | None = None) -> None:
        """Zeigt das Menü, bis „0“ gewählt wird oder die Eingabe endet (Strg+D, Strg+C)."""
        self.console.rule("[bold]LEGO-Mosaik – interaktiver Modus[/bold]")
        actions = self._actions()
        try:
            if image is not None:
                self._attempt(lambda: self.load_image(image))
            while self._step(actions):
                pass  # _step liefert False, sobald beendet werden soll
        except EOFError, KeyboardInterrupt:  # PEP 758: mehrere Typen ohne Klammern
            pass  # Eingabe beendet: wie „0“ behandeln
        self.console.print("Tschüss!")

    def _step(self, actions: dict[str, tuple[str, Callable[[], object]]]) -> bool:
        """Ein Durchlauf: Menü zeigen, Auswahl lesen, Aktion ausführen. False = beenden."""
        self._print_menu(actions)
        choice = Prompt.ask("Auswahl", console=self.console).strip().lower()
        if choice in EXIT_CHOICES:
            return False
        if choice not in actions:
            self.console.print(f"[red]Unbekannte Auswahl „{escape(choice)}“.[/red]")
        else:
            self._attempt(actions[choice][1])
        return True

    def _actions(self) -> dict[str, tuple[str, Callable[[], object]]]:
        """Menüpunkte: Taste → (Beschriftung, Aktion)."""
        actions: dict[str, tuple[str, Callable[[], object]]] = {"1": ("Bild laden", self.ask_image)}
        for number, (title, fields) in enumerate(GROUPS, start=2):
            # functools.partial legt die Argumente jetzt fest; ein lambda würde erst beim
            # Aufruf nachsehen und dann bei allen Menüpunkten die letzte Gruppe finden
            actions[str(number)] = (title, functools.partial(self.edit, title, fields))
        extra: tuple[tuple[str, Callable[[], object]], ...] = (
            ("Einstellungen anzeigen", self.show_settings),
            ("Vorschau im Terminal", self.preview),
            ("Mosaik erstellen und Dateien ausgeben", self.create),
            ("Farbpalette anzeigen", self.show_palette),
            ("Formate anzeigen", self.show_formats),
            ("Preset laden", self.load_preset),
            ("Einstellungen als Preset speichern", self.save_preset),
        )
        if self._price_factory is not None:
            extra += (("Preis bei BrickLink schätzen (zuletzt erstelltes Mosaik)", self.prices),)
        for number, entry in enumerate(extra, start=len(GROUPS) + 2):
            actions[str(number)] = entry
        return actions

    def _print_menu(self, actions: dict[str, tuple[str, Callable[[], object]]]) -> None:
        image = "–" if self.image is None else f"{self.image.name} ({self._image_size()})"
        self.console.print(f"\n[bold]Bild:[/bold] {escape(image)}")
        if self.image is not None:
            self.console.print(f"[bold]Name:[/bold] {escape(self.title(self.image))}")
        for key, (label, _) in actions.items():
            self.console.print(f"  [cyan]{key:>2}[/cyan]  {label}")
        self.console.print("  [cyan] 0[/cyan]  Beenden")

    def _attempt(self, action: Callable[[], object]) -> None:
        """Führt eine Aktion aus; fachliche Fehler werden gemeldet, das Menü läuft weiter."""
        try:
            action()
        except LegoMosaicError as error:
            if self._debug:
                raise
            self.console.print(f"[bold red]Fehler:[/bold red] {escape(str(error))}")

    # --- Aktionen ----------------------------------------------------------------

    def ask_image(self) -> None:
        """Fragt nach einem Bildpfad und lädt das Bild."""
        answer = Prompt.ask("Pfad zur Bilddatei", console=self.console).strip()
        if answer:
            self.load_image(Path(answer.strip("'\"")).expanduser())

    def load_image(self, path: Path) -> None:
        """Prüft das Bild (Format, Größe) und merkt es sich für Vorschau und Ausgabe.

        Raises:
            InvalidImageError: Wenn die Datei fehlt oder kein Bild ist.
        """
        self.image_info = self.pipeline.inspect_image(path)
        self.image = path
        self.console.print(
            f"[green]Bild geladen:[/green] {escape(path.name)}, {self._image_size()}, "
            f"Seitenverhältnis {self.image_info.aspect_ratio:.2f}"
        )
        studs = self.run_config().layout.plate_studs
        suggestions = ", ".join(
            f"{f.name} ({f.plates_wide * studs} × {f.plates_high * studs})"
            for f in suggest_formats(self.image_info.aspect_ratio)
        )
        self.console.print(f"Passende Formate: {suggestions}")

    def _image_size(self) -> str:
        info = self.image_info
        return "" if info is None else f"{info.width} × {info.height} Pixel, {info.format}"

    def edit(self, title: str, fields: tuple[_Field, ...]) -> None:
        """Fragt alle Felder einer Gruppe ab; ungültige Gesamtangaben werden verworfen."""
        self.console.rule(title)
        self.console.print(f"[dim]Enter = beibehalten, „{RESET}“ = Standardwert[/dim]")
        if title == "Verfahren":  # registrierte Namen zeigen, damit man sie nicht raten muss
            for kind, names in self.pipeline.method_names.items():
                self.console.print(f"[dim]{kind}: {', '.join(names)}[/dim]")
        current = self.run_config()
        # Any: Jedes Feld hat einen anderen Typ; geprüft wird gleich in apply_overrides
        changes: dict[str, Any] = {}
        for field in fields:
            value = self._ask(field, current)
            if value is not _KEEP:
                changes[field.name] = value
        _exclusive_size(changes)
        candidate = replace(self.inputs, **changes)
        # Erst prüfen, dann übernehmen: bei einem Fehler bleiben die alten Eingaben gültig
        apply_overrides(self.config, parse_overrides(candidate))
        self.inputs = candidate
        self.console.print("[green]Übernommen.[/green]")

    def _ask(self, field: _Field, current: AppConfig) -> object:
        """Eine Frage; liefert den neuen Wert oder ``_KEEP``."""
        shown = escape(str(field.current(current, self.inputs)))
        while True:
            answer = Prompt.ask(
                f"{field.question} [dim](aktuell: {shown})[/dim]",
                console=self.console,
                default="",
                show_default=False,
            ).strip()
            if not answer:
                return _KEEP
            if answer == RESET:
                return getattr(_DEFAULT_INPUT, field.name)  # Standard: None bzw. False
            try:
                return field.convert(answer)
            except ValueError:
                self.console.print(f"[red]Ungültige Eingabe „{escape(answer)}“.[/red]")

    def show_settings(self) -> None:
        """Tabelle aller wirksamen Einstellungen, gruppiert wie im Menü."""
        current = self.run_config()
        table = Table("Einstellung", "Wert", title="Aktuelle Einstellungen")
        for title, fields in GROUPS:
            table.add_section()
            table.add_row(f"[bold]{title}[/bold]", "")
            for field in fields:
                table.add_row(field.question, escape(str(field.current(current, self.inputs))))
        self.console.print(table)

    def preview(self) -> None:
        """Berechnet das Mosaik ohne Dateien und zeigt es farbig im Terminal."""
        image = self._require_image()
        if image is None:
            return
        with self.console.status("Berechne Vorschau …") as status:
            mosaic = self.pipeline.preview(
                image,
                parse_overrides(self.inputs),
                title=self.title(image),
                on_step=lambda label: status.update(label),
            )
        self.console.print(mosaic_preview(mosaic, self.console.width))
        self.console.print(
            f"{mosaic.width} × {mosaic.height} Noppen · {len(mosaic.used_colors())} Farben · "
            f"{len(mosaic.surface)} sichtbare Teile · {len(mosaic.substrate)} Unterbau-Platten"
        )

    def create(self) -> None:
        """Erzeugt das Mosaik mit allen konfigurierten Ausgaben im Zielordner."""
        image = self._require_image()
        if image is None:
            return
        with self.console.status("Starte …") as status:
            result = self.pipeline.run(
                image,
                parse_overrides(self.inputs),
                title=self.title(image),
                on_step=lambda label: status.update(label),
            )
        print_summary(self.console, result)
        self._last_result_dir = result.target_dir

    def prices(self) -> None:
        """Schätzt den Preis des zuletzt erstellten Mosaiks und aktualisiert die Wanted List."""
        if self._price_factory is None:
            return
        if self._last_result_dir is None:
            self.console.print("[yellow]Bitte zuerst ein Mosaik erstellen.[/yellow]")
            return
        service = self._price_factory(self.config)
        with self.console.status("Frage Preise bei BrickLink ab …"):
            estimate = service.estimate(self._last_result_dir)
        print_prices(self.console, estimate, service.pricing.upload_url)

    def load_preset(self) -> None:
        """Zeigt die Presets und übernimmt die Einstellungen des gewählten (Name bleibt)."""
        library = Library.open(self.config.storage)
        presets = library.presets.list()
        print_presets(self.console, presets)
        if not presets:
            return
        answer = Prompt.ask("Preset (Name oder ID, Enter = abbrechen)", console=self.console)
        if not answer.strip():
            return
        resolved = library.resolve({}, preset=answer.strip())
        candidate = replace(resolved.inputs, title=self.inputs.title)
        apply_overrides(self.config, parse_overrides(candidate))  # erst prüfen, dann übernehmen
        self.inputs = candidate
        name = resolved.preset.name if resolved.preset else answer
        library.presets.record_use(answer.strip())
        self.console.print(f"[green]Preset „{escape(name)}“ übernommen.[/green]")

    def save_preset(self) -> None:
        """Speichert die aktuellen Eingaben (ohne Name des Mosaiks) als Preset."""
        name = Prompt.ask("Name des Presets", console=self.console).strip()
        if not name:
            return
        values = form_values(self.inputs)
        values.pop("titel", None)
        library = Library.open(self.config.storage)
        settings = library.preset_settings(values, self.pipeline.check)
        preset = library.presets.create(name, "", settings)
        self.console.print(f"[green]Preset gespeichert:[/green] {escape(preset.id)}")

    def show_formats(self) -> None:
        """Formate mit Größe in Noppen bei der aktuellen Plattengröße."""
        print_formats(self.console, self.run_config().layout.plate_studs)

    def show_palette(self) -> None:
        """Palette für die aktuell gewählten Teilarten und Farbkategorien."""
        print_palette(self.console, self.pipeline.palette(parse_overrides(self.inputs)))

    def title(self, image: Path) -> str:
        """Name des Mosaiks: eingegeben oder der Name der Bilddatei."""
        return run_title(self.inputs, image.stem)

    def _require_image(self) -> Path | None:
        if self.image is None:
            self.console.print("[yellow]Bitte zuerst ein Bild laden (Menüpunkt 1).[/yellow]")
        return self.image


def _exclusive_size(changes: dict[str, Any]) -> None:
    """Format und eigene Größe schließen sich aus: Die neue Angabe verdrängt die alte.

    Stehen beide neu in ``changes``, bleibt alles, wie es ist; ``parse_overrides``
    meldet dann den Widerspruch.
    """
    new_format = changes.get(FORMAT_FIELD) is not None
    new_size = any(changes.get(name) is not None for name in SIZE_FIELDS)
    if new_format and not new_size:
        changes.update(dict.fromkeys(SIZE_FIELDS))  # None = Größe aus dem Format
    elif new_size and not new_format:
        changes[FORMAT_FIELD] = None


_KEEP = object()  # Markierung „Wert nicht ändern“ (eindeutig, kann nie eine Eingabe sein)
_DEFAULT_INPUT = OverrideInput()  # liefert die Standardwerte der Eingabefelder
