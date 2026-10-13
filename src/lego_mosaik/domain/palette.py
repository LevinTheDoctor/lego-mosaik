"""Palette bauen: welche Farben darf das Dithering verwenden?

Reine Funktionen ohne Seiteneffekte: gleiche Eingabe, gleiche Ausgabe. Das macht
sie trivial testbar und später leicht nach Java oder Rust portierbar.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence

lazy import numpy as np

from lego_mosaik.domain.models import ColorCategory, IndexGrid, LegoColor, PartCatalog, PartKind

# Rebrickable-Sonderfarben, die nie in eine Palette gehören:
# -1 = "[Unknown]", 9999 = "[No Color/Any Color]"
SPECIAL_COLOR_IDS = frozenset({-1, 9999})
_WORDS = re.compile(r"[A-Za-z0-9]+")
SINGLE_WORD_CODE = 3  # Länge des Kürzels bei einem Wort („Black“ → „Bla“)


def build_palette(
    catalog: PartCatalog,
    part_kinds: Iterable[PartKind],
    *,
    categories: Iterable[ColorCategory] = (ColorCategory.SOLID,),
    include_color_ids: Iterable[int] = (),
    exclude_color_ids: Iterable[int] = (),
) -> tuple[LegoColor, ...]:
    """Bestimmt alle Farben, die als Mosaikpixel taugen.

    Eine Farbe kommt nur in die Palette, wenn

    - sie nicht ausgeschlossen ist (Sonderfarben und ``exclude_color_ids``),
    - ihre Kategorie erlaubt ist oder ihre ID in ``include_color_ids`` steht,
    - ein LDraw-Code bekannt ist (sonst wäre sie in Studio falsch) und
    - es **mindestens ein 1×1-Teil** der erlaubten Teilarten in ihr gibt. Sonst
      ließe sich ein einzelnes Pixel dieser Farbe nicht belegen.

    Args:
        catalog: Der Teilekatalog.
        part_kinds: Erlaubte Teilarten.
        categories: Erlaubte Farbkategorien (Standard: nur Solid).
        include_color_ids: Farb-IDs, die unabhängig von der Kategorie erlaubt sind.
        exclude_color_ids: Farb-IDs, die nie verwendet werden sollen (hat Vorrang).

    Returns:
        Die Palette, sortiert nach Rebrickable-ID (deterministisch).
    """
    # Der * in der Signatur macht alle folgenden Parameter "keyword-only":
    # Sie müssen beim Aufruf mit Namen angegeben werden (lesbarer, weniger Verwechslung)
    kinds = tuple(part_kinds)
    allowed_categories, included = frozenset(categories), frozenset(include_color_ids)
    excluded = SPECIAL_COLOR_IDS | frozenset(exclude_color_ids)  # | = Vereinigung zweier Mengen
    palette = [
        color
        for color in catalog.colors.values()
        if color.id not in excluded
        and (color.category in allowed_categories or color.id in included)
        and color.ldraw_code is not None
        and _has_single_stud_part(catalog, color.id, kinds)
    ]
    return tuple(sorted(palette, key=lambda color: color.id))


def _has_single_stud_part(catalog: PartCatalog, color_id: int, kinds: Sequence[PartKind]) -> bool:
    """True, wenn es in der Farbe ein 1×1-Teil einer erlaubten Art gibt."""
    # any() bricht beim ersten Treffer ab (Kurzschlussauswertung)
    return any(part.is_single_stud for part in catalog.parts_in_color(color_id, kinds))


def most_used_colors(
    color_grid: IndexGrid, palette: Sequence[LegoColor], max_colors: int
) -> tuple[LegoColor, ...]:
    """Die ``max_colors`` am häufigsten genutzten Farben eines Rasters.

    Bei Gleichstand gewinnt die kleinere Rebrickable-ID, damit das Ergebnis
    deterministisch bleibt.

    Args:
        color_grid: H×W-Raster mit Indizes in ``palette``.
        palette: Die Palette, auf die sich die Indizes beziehen.
        max_colors: Maximale Anzahl Farben.

    Returns:
        Die häufigsten Farben, sortiert nach Rebrickable-ID.
    """
    # bincount zählt, wie oft jeder Index 0..P-1 vorkommt; minlength sorgt dafür,
    # dass auch ungenutzte Farben (Anzahl 0) einen Eintrag bekommen
    counts = np.bincount(color_grid.ravel(), minlength=len(palette))
    # Sortierschlüssel (-Anzahl, ID): absteigend nach Anzahl, bei Gleichstand nach ID
    ranked = sorted(range(len(palette)), key=lambda index: (-counts[index], palette[index].id))
    chosen = [palette[index] for index in ranked[:max_colors] if counts[index] > 0]
    return tuple(sorted(chosen, key=lambda color: color.id))


def display_name(color: LegoColor) -> str:
    """Name einer Farbe in Anleitung und Teileliste: LEGO-Name, sonst Rebrickable-Name."""
    return color.lego_name or color.name


def color_codes(
    colors: Iterable[LegoColor], name: Callable[[LegoColor], str] = display_name
) -> Mapping[int, str]:
    """Kurze, eindeutige Farbkürzel für Anleitung und Teileliste (Farb-ID → Kürzel).

    Das Kürzel kommt aus dem angezeigten Namen (``name``, Standard: LEGO-Farbname), damit
    beides zusammenpasst. Mehrere Wörter ergeben die Anfangsbuchstaben („Medium Stone
    Grey“ → „MSG“), ein
    Wort die ersten drei Buchstaben („Black“ → „Bla“). Gibt es ein Kürzel doppelt
    („Dark Blue“ und „Dark Brown“), bekommt das letzte Wort zwei Buchstaben („DBl“,
    „DBr“); hilft das nicht, wird durchnummeriert. So lassen sich ähnliche Töne (vier
    Grau, mehrere Blau) in den Bauschritten unterscheiden.
    """
    ordered = sorted(colors, key=lambda color: color.id)
    candidates = {color.id: _code_candidates(name(color)) for color in ordered}
    codes: dict[int, str] = {}
    for level in range(2):
        taken = [candidates[color.id][level] for color in ordered if color.id not in codes]
        for color in ordered:
            code = candidates[color.id][level]
            if color.id not in codes and (taken.count(code) == 1 or level == 1):
                codes[color.id] = code
    # was jetzt noch doppelt ist, wird durchnummeriert: „DB“, „DB2“, …
    seen: dict[str, int] = {}
    for color in ordered:
        code = codes[color.id]
        seen[code] = seen.get(code, 0) + 1
        if seen[code] > 1:
            codes[color.id] = f"{code}{seen[code]}"
    return codes


def _code_candidates(name: str) -> tuple[str, str]:
    """Kurzes und etwas längeres Kürzel eines Farbnamens."""
    words = _WORDS.findall(name) or [name or "?"]
    if len(words) == 1:
        word = words[0]
        return word[:SINGLE_WORD_CODE].capitalize(), word[: SINGLE_WORD_CODE + 1].capitalize()
    initials = "".join(word[0].upper() for word in words)
    return initials, initials[:-1] + words[-1][:2].capitalize()
