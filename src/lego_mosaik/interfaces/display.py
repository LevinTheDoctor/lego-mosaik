"""Gemeinsame Rich-Ausgaben für die Kommandozeile und den interaktiven Modus."""

lazy from rich.console import Console
lazy from rich.table import Table
lazy from rich.text import Text

from lego_mosaik.domain.layout import FORMATS, ArtFormat
from lego_mosaik.domain.models import LegoColor, Mosaic
lazy from lego_mosaik.pipeline import MosaicResult

SWATCH = "      "  # Leerzeichen, die mit der Farbe als Hintergrund ein Farbfeld ergeben
UPPER_HALF_BLOCK = "▀"  # obere Hälfte farbig (Vordergrund), untere = Hintergrund


def print_summary(console: Console, result: MosaicResult) -> None:
    """Zusammenfassung: Größe, Unterbau, Farben, Teile, Einsparung, erzeugte Dateien."""
    stats, mosaic = result.stats, result.mosaic
    table = Table(title=f"Mosaik „{mosaic.title}“ fertig", show_header=False)
    table.add_row("Größe", f"{mosaic.width} × {mosaic.height} Noppen")
    table.add_row("Bildfläche", f"{stats['bild_breite']} × {stats['bild_hoehe']} Noppen")
    table.add_row("Unterbau", _substrate_text(mosaic))
    table.add_row("Farben", str(stats["farben"]))
    parts = f"{stats['parts']} sichtbar"
    if stats.get("formteile"):
        parts += f" (davon {stats['formteile']} Formteile)"
    if stats["stuetzteile"]:  # nur bei erhöhtem Rahmen
        parts += f", {stats['stuetzteile']} unter dem Rahmen"
    if stats.get("relief_teile"):
        parts += f", {stats['relief_teile']} als Relief darunter"
    if stats.get("unterlagen"):
        parts += f", {stats['unterlagen']} Unterlagen unter Formteilen"
    table.add_row("Teile", f"{stats['teile']} insgesamt: {parts}")
    table.add_row("Einsparung", f"{stats['savings_percent']} % gegenüber nur 1×1")
    durations = stats["dauer_sekunden"]
    assert isinstance(durations, dict)  # schränkt den Typ für mypy ein
    table.add_row("Dauer", f"{sum(durations.values()):.1f} s")
    table.add_row("Ordner", str(result.target_dir))
    console.print(table)
    for path in sorted(result.target_dir.iterdir()):
        console.print(f"  • {path.name}")


def _substrate_text(mosaic: Mosaic) -> str:
    """Z. B. „9 × Brick Special 16 x 16 …, 24 Technic-Pins“ oder „ohne“."""
    if not mosaic.substrate:
        return "ohne"
    text = f"{len(mosaic.substrate)} × {mosaic.substrate[0].part.name}"
    if mosaic.connectors:
        text += f", verbunden mit {len(mosaic.connectors)} Technic-Pins"
    return text


def print_formats(
    console: Console, plate_studs: int, formats: tuple[ArtFormat, ...] = FORMATS
) -> None:
    """Tabelle der Formate mit Größe in Noppen bei der angegebenen Plattengröße."""
    table = Table("Name", "Platten", "Noppen", "Beschreibung", title="Formate")
    for art_format in formats:
        wide, high = art_format.plates_wide, art_format.plates_high
        studs = f"{wide * plate_studs} × {high * plate_studs}"
        table.add_row(art_format.name, f"{wide} × {high}", studs, art_format.description)
    console.print(table)


def print_palette(console: Console, palette: tuple[LegoColor, ...]) -> None:
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


def mosaic_preview(mosaic: Mosaic, max_columns: int) -> Text:
    """Das Mosaik als farbige Halbblöcke: eine Textzeile zeigt zwei Noppenreihen.

    Ein Terminalzeichen ist etwa doppelt so hoch wie breit. Mit „▀“ (obere Hälfte in
    der Vordergrundfarbe, untere in der Hintergrundfarbe) wird jede Noppe quadratisch.
    Ist das Mosaik breiter als das Terminal, wird nur jede ``step``-te Noppe gezeigt.
    """
    step = max(1, -(-mosaic.width // max_columns))  # aufgerundete Division
    rows = list(range(0, mosaic.height, step))
    cols = list(range(0, mosaic.width, step))
    text = Text()
    # rows[::2] nimmt jede zweite Zeile: die oberen Hälften der Zeichen
    for index, upper in enumerate(rows[::2]):
        lower = rows[2 * index + 1] if 2 * index + 1 < len(rows) else None
        for col in cols:
            style = mosaic.color_at(upper, col).hex
            if lower is not None:
                style += f" on {mosaic.color_at(lower, col).hex}"
            text.append(UPPER_HALF_BLOCK, style=style)
        text.append("\n")
    return text
