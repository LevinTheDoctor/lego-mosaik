"""Gespeicherte Bibliothek: eigene Farbpaletten und Presets (Einstellungen mit Bewertung).

Nur die Fachbegriffe und Regeln (Namen, Sortierung); Laden und Speichern steht in
``storage/``.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from lego_mosaik.domain.errors import ConfigError

MAX_NAME_LENGTH = 60
MAX_DESCRIPTION_LENGTH = 500
MAX_RATING = 5  # Sterne
UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_NOT_SLUG = re.compile(r"[^a-z0-9]+")

type SettingValue = str | int | float | bool
"""Wert eines Formularfelds von ``POST /mosaike``."""


@dataclass(frozen=True, slots=True)
class ColorPalette:
    """Eigene Farbpalette: Das Bild wird nur mit diesen Farben gedithert.

    Attributes:
        id: Kennung aus dem Namen (``slug``), z. B. „pastell-toene“.
        name: Name zum Anzeigen.
        description: Freitext.
        color_ids: Rebrickable-Farb-IDs.
    """

    id: str
    name: str
    description: str
    color_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Preset:
    """Gespeicherte Einstellungen für ``POST /mosaike`` bzw. ``erstellen``.

    Attributes:
        id: Kennung aus dem Namen.
        name: Name zum Anzeigen.
        description: Freitext.
        settings: Formularfeld → Wert, z. B. ``{"stil": "punkte", "format": "quadrat"}``.
        favorite: Als Favorit markiert (steht in Listen oben).
        rating: Bewertung 0–5 Sterne (0 = unbewertet).
        uses: Wie oft das Preset für ein Mosaik benutzt wurde.
        created: Angelegt (UTC).
        changed: Zuletzt geändert (UTC).
    """

    id: str
    name: str
    description: str
    settings: Mapping[str, SettingValue]
    created: datetime
    changed: datetime
    favorite: bool = False
    rating: int = 0
    uses: int = 0


class PresetOrder(StrEnum):
    """Sortierung der Preset-Liste (Favoriten stehen immer zuerst)."""

    RATING = "bewertung"  # beste zuerst, dann am häufigsten benutzt
    USES = "nutzung"  # am häufigsten benutzt zuerst
    NAME = "name"  # alphabetisch
    NEWEST = "neu"  # zuletzt geändert zuerst


def slug(name: str) -> str:
    """Kennung aus einem Namen: „Pastell Töne!“ → „pastell-toene“."""
    return _NOT_SLUG.sub("-", name.strip().lower().translate(UMLAUTS)).strip("-")


def check_name(name: str, label: str) -> str:
    """Bereinigter Name; leer, zu lang oder ohne Buchstaben/Ziffern ist ein Fehler.

    Raises:
        ConfigError: Bei ungültigem Namen.
    """
    cleaned = name.strip()
    if not slug(cleaned):
        raise ConfigError(f"Der Name {label} braucht mindestens einen Buchstaben oder eine Ziffer.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ConfigError(f"Der Name {label} darf höchstens {MAX_NAME_LENGTH} Zeichen lang sein.")
    return cleaned


def check_description(text: str) -> str:
    """Bereinigte Beschreibung.

    Raises:
        ConfigError: Wenn sie zu lang ist.
    """
    if len(text) > MAX_DESCRIPTION_LENGTH:
        raise ConfigError(
            f"Die Beschreibung darf höchstens {MAX_DESCRIPTION_LENGTH} Zeichen lang sein."
        )
    return text.strip()


def check_rating(rating: int) -> int:
    """Bewertung 0–5.

    Raises:
        ConfigError: Außerhalb des Bereichs.
    """
    if not 0 <= rating <= MAX_RATING:
        raise ConfigError(f"Die Bewertung muss zwischen 0 und {MAX_RATING} liegen.")
    return rating


def sort_presets(
    presets: Iterable[Preset], order: PresetOrder, *, favorites_only: bool = False
) -> tuple[Preset, ...]:
    """Favoriten zuerst, dann nach ``order``; bei Gleichstand nach Name."""
    chosen = [p for p in presets if p.favorite or not favorites_only]
    chosen.sort(key=lambda p: p.name.casefold())  # stabile Sortierung: Name als letzter Schlüssel
    match order:
        case PresetOrder.RATING:
            chosen.sort(key=lambda p: (-p.rating, -p.uses))
        case PresetOrder.USES:
            chosen.sort(key=lambda p: -p.uses)
        case PresetOrder.NEWEST:
            chosen.sort(key=lambda p: p.changed, reverse=True)
        case PresetOrder.NAME:
            pass
    chosen.sort(key=lambda p: not p.favorite)  # False < True: Favoriten nach vorne
    return tuple(chosen)
