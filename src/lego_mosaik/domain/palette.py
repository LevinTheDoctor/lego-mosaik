"""Palette bauen: welche Farben darf das Dithering verwenden?

Reine Funktionen ohne Seiteneffekte: gleiche Eingabe, gleiche Ausgabe. Das macht
sie trivial testbar und später leicht nach Java oder Rust portierbar.
"""

from collections.abc import Iterable, Sequence

lazy import numpy as np

from lego_mosaik.domain.models import IndexGrid, LegoColor, PartCatalog, PartKind

# Rebrickable-Sonderfarben, die nie in eine Palette gehören:
# -1 = "[Unknown]", 9999 = "[No Color/Any Color]"
SPECIAL_COLOR_IDS = frozenset({-1, 9999})


def build_palette(
    catalog: PartCatalog,
    part_kinds: Iterable[PartKind],
    *,
    exclude_transparent: bool = True,
    exclude_color_ids: Iterable[int] = (),
) -> tuple[LegoColor, ...]:
    """Bestimmt alle Farben, die als Mosaikpixel taugen.

    Eine Farbe kommt nur in die Palette, wenn es in ihr **mindestens ein 1×1-Teil**
    der erlaubten Teilarten gibt. Sonst ließe sich ein einzelnes Pixel dieser Farbe
    nicht belegen.

    Args:
        catalog: Der Teilekatalog.
        part_kinds: Erlaubte Teilarten.
        exclude_transparent: Transparente Farben weglassen.
        exclude_color_ids: Farb-IDs, die nie verwendet werden sollen.

    Returns:
        Die Palette, sortiert nach Rebrickable-ID (deterministisch).
    """
    # Der * in der Signatur macht alle folgenden Parameter "keyword-only":
    # Sie müssen beim Aufruf mit Namen angegeben werden (lesbarer, weniger Verwechslung)
    kinds = tuple(part_kinds)
    excluded = SPECIAL_COLOR_IDS | frozenset(exclude_color_ids)  # | = Vereinigung zweier Mengen
    palette = [
        color
        for color in catalog.colors.values()
        if color.id not in excluded
        and not (exclude_transparent and color.is_transparent)
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
